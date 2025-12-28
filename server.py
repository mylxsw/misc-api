# coding=utf-8
"""Expose CosyVoice TTS as an HTTP API.

POST /v1/voice/cosyvoice
Payload: {"text": "要转换的文本", "voice": "音色(可选)"}
Response: {"voice_b64": "base64编码的音频数据", "request_id": "...", "first_package_delay_ms": 123}
"""
import base64
import os
from typing import Tuple, List
from io import BytesIO

from flask import Flask, jsonify, request
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer
import requests
from PIL import Image
import asyncio
import threading
import json
import time
import uuid
import redis
from lib.podcast.client import PodcastTTSClient

# Resolve and validate the dashscope API key early so we can return a clearer error
# instead of a TypeError from the SDK when it tries to concat None.
_resolved_api_key = os.getenv("DASHSCOPE_API_KEY") or dashscope.api_key
if not _resolved_api_key:
    raise RuntimeError("DASHSCOPE_API_KEY is not set; please export it for the service to start.")
dashscope.api_key = _resolved_api_key

app = Flask(__name__)

DEFAULT_MODEL = "cosyvoice-v2"
DEFAULT_VOICE = "libai_v2"

_volc_appid = os.getenv("VOLC_APPID")
_volc_access_token = os.getenv("VOLC_ACCESS_TOKEN")

_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(_redis_url)
REDIS_TTL = 7 * 24 * 3600  # 7 days


def synthesize(text: str, voice: str) -> Tuple[bytes, str, int]:
    """Run CosyVoice TTS and return audio bytes plus request metadata."""
    synthesizer = SpeechSynthesizer(model=DEFAULT_MODEL, voice=voice)
    audio = synthesizer.call(text)
    return audio, synthesizer.get_last_request_id(), synthesizer.get_first_package_delay()


@app.route("/v1/voice/cosyvoice", methods=["POST"])
def cosyvoice_endpoint():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    voice = payload.get("voice") or DEFAULT_VOICE

    if not text:
        return jsonify({"error": "parameter 'text' is required"}), 400

    try:
        audio, request_id, first_pkg_delay = synthesize(text=text, voice=voice)
    except Exception as exc:  # dashscope errors propagate here
        return jsonify({"error": str(exc)}), 500

    voice_b64 = base64.b64encode(audio).decode("ascii")
    return jsonify(
        {
            "voice_b64": voice_b64,
            "request_id": request_id,
            "first_package_delay_ms": first_pkg_delay,
        }
    )


def stitch_images(image_list: List[str], direction: str = "horizontal") -> str:
    images = []
    for img_str in image_list:
        try:
            if img_str.startswith("http://") or img_str.startswith("https://"):
                response = requests.get(img_str, timeout=10)
                response.raise_for_status()
                img_data = response.content
            else:
                # Handle base64
                if "," in img_str:
                    img_str = img_str.split(",", 1)[1]
                img_data = base64.b64decode(img_str)
            
            images.append(Image.open(BytesIO(img_data)))
        except Exception as e:
            print(f"Error loading image: {e}")
            continue

    if not images:
        raise ValueError("No valid images to stitch")

    if direction == "vertical":
        width = max(img.width for img in images)
        height = sum(img.height for img in images)
        result = Image.new("RGB", (width, height))
        y_offset = 0
        for img in images:
            result.paste(img, (0, y_offset))
            y_offset += img.height
    else:  # horizontal
        width = sum(img.width for img in images)
        height = max(img.height for img in images)
        result = Image.new("RGB", (width, height))
        x_offset = 0
        for img in images:
            result.paste(img, (x_offset, 0))
            x_offset += img.width

    buffered = BytesIO()
    result.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("ascii")


@app.route("/v1/voice/podcast", methods=["POST"])
def podcast_endpoint():
    payload = request.get_json(silent=True) or {}
    scripts = payload.get("scripts")
    use_head_music = payload.get("use_head_music") or False
    use_tail_music = payload.get("use_tail_music") or False
    
    if not scripts or not isinstance(scripts, list):
         return jsonify({"error": "parameter 'scripts' is required and must be a list"}), 400

    if not _volc_appid or not _volc_access_token:
         return jsonify({"error": "VOLC_APPID or VOLC_ACCESS_TOKEN not set on server"}), 500

    task_id = str(uuid.uuid4())
    
    # Initialize task status in Redis
    task_info = {
        "status": "processing",
        "created_at": time.time(),
        "task_id": task_id
    }
    redis_client.setex(f"podcast_task:{task_id}", REDIS_TTL, json.dumps(task_info))

    # Start background task
    thread = threading.Thread(
        target=process_podcast_task,
        args=(task_id, scripts, use_head_music, use_tail_music)
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/v1/voice/podcast/<task_id>", methods=["GET"])
def query_podcast_task(task_id):
    data = redis_client.get(f"podcast_task:{task_id}")
    if not data:
        return jsonify({"error": "Task not found"}), 404
        
    return jsonify(json.loads(data))


def process_podcast_task(task_id, scripts, use_head_music, use_tail_music):
    try:
        client = PodcastTTSClient(appid=_volc_appid, access_token=_volc_access_token)
        # Using asyncio.run to call async code
        audio_bytes = asyncio.run(client.generate_audio(
            scripts, 
            use_head_music=use_head_music, 
            use_tail_music=use_tail_music
        ))
        voice_b64 = base64.b64encode(audio_bytes).decode("ascii")
        
        # Update success status
        task_info = {
            "status": "success",
            "voice_b64": voice_b64,
            "created_at": time.time(), # Update time or keep original? Keeping simple.
            "task_id": task_id
        }
    except Exception as e:
        task_info = {
            "status": "failed",
            "error": str(e),
            "created_at": time.time(),
            "task_id": task_id
        }
    
    redis_client.setex(f"podcast_task:{task_id}", REDIS_TTL, json.dumps(task_info))


@app.route("/v1/image/stitch", methods=["POST"])
def stitch_endpoint():
    payload = request.get_json(silent=True) or {}
    images = payload.get("images") or []
    direction = payload.get("direction") or "horizontal"

    if not images or not isinstance(images, list):
         return jsonify({"error": "parameter 'images' is required and must be a list"}), 400

    try:
        result_b64 = stitch_images(images, direction)
        return jsonify({"image_b64": result_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def create_app() -> Flask:
    """Flask factory for WSGI/ASGI servers."""
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
