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
from werkzeug.exceptions import RequestEntityTooLarge
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
from lib.wechat import (
    WeChatConverter,
    load_theme,
    list_themes,
    preview_html,
    rewrite_image_srcs,
    get_access_token,
    upload_image_bytes,
    upload_thumb_bytes,
    load_image_bytes,
    create_draft,
    delete_draft,
    get_draft,
    list_drafts,
    update_draft,
    WeChatDraftAPIError,
)
from fishaudio import FishAudio
from fishaudio.types import TTSConfig, Prosody
from lib.image_generation import (
    ImageGenerationError,
    generate_normalized_image,
    get_image_model_catalog,
    get_provider,
    normalize_image_aspect_ratio,
    normalize_input_images,
)
from lib.object_storage import ObjectStorageError, S3ImageStorage
from lib.image_generation_history import save_image_history_async

# Configure DashScope when available. The key is validated on CosyVoice calls so
# unrelated APIs can still run without TTS credentials.
_resolved_api_key = os.getenv("DASHSCOPE_API_KEY") or dashscope.api_key
if _resolved_api_key:
    dashscope.api_key = _resolved_api_key

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(
    os.getenv("MAX_REQUEST_BYTES", str(85 * 1024 * 1024))
)


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "request body exceeds the configured size limit"}), 413


DEFAULT_MODEL = "cosyvoice-v2"
DEFAULT_VOICE = "libai_v2"

_volc_appid = os.getenv("VOLC_APPID")
_volc_access_token = os.getenv("VOLC_ACCESS_TOKEN")

_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(_redis_url)
REDIS_TTL = 7 * 24 * 3600  # 7 days


def synthesize(text: str, voice: str, model: str = DEFAULT_MODEL, **kwargs) -> Tuple[bytes, str, int]:
    """Run CosyVoice TTS and return audio bytes plus request metadata."""
    if not _resolved_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    synthesizer = SpeechSynthesizer(model=model, voice=voice, **kwargs)
    audio = synthesizer.call(text)
    return audio, synthesizer.get_last_request_id(), synthesizer.get_first_package_delay()


@app.route("/v1/voice/cosyvoice", methods=["POST"])
def cosyvoice_endpoint():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    voice = payload.get("voice") or DEFAULT_VOICE
    model = payload.get("model") or DEFAULT_MODEL
    
    # Optional parameters
    kwargs = {}
    for param in ["volume", "speech_rate", "pitch_rate", "instruction", "language_hints"]:
        if param in payload:
            kwargs[param] = payload[param]

    if not text:
        return jsonify({"error": "parameter 'text' is required"}), 400

    try:
        audio, request_id, first_pkg_delay = synthesize(text=text, voice=voice, model=model, **kwargs)
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


def process_cosyvoice_task(task_id, text, voice, model, kwargs):
    try:
        audio, request_id, first_pkg_delay = synthesize(text=text, voice=voice, model=model, **kwargs)
        voice_b64 = base64.b64encode(audio).decode("ascii")
        
        task_info = {
            "status": "success",
            "voice_b64": voice_b64,
            "request_id": request_id,
            "first_package_delay_ms": first_pkg_delay,
            "created_at": time.time(),
            "task_id": task_id
        }
    except Exception as e:
        task_info = {
            "status": "failed",
            "error": str(e),
            "created_at": time.time(),
            "task_id": task_id
        }
    
    redis_client.setex(f"cosyvoice_task:{task_id}", REDIS_TTL, json.dumps(task_info))


@app.route("/v1/voice/cosyvoice/async", methods=["POST"])
def async_cosyvoice_endpoint():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    voice = payload.get("voice") or DEFAULT_VOICE
    model = payload.get("model") or DEFAULT_MODEL
    
    # Optional parameters
    kwargs = {}
    for param in ["volume", "speech_rate", "pitch_rate", "instruction", "language_hints"]:
        if param in payload:
            kwargs[param] = payload[param]

    if not text:
        return jsonify({"error": "parameter 'text' is required"}), 400

    task_id = str(uuid.uuid4())
    
    task_info = {
        "status": "processing",
        "created_at": time.time(),
        "task_id": task_id
    }
    redis_client.setex(f"cosyvoice_task:{task_id}", REDIS_TTL, json.dumps(task_info))

    thread = threading.Thread(
        target=process_cosyvoice_task,
        args=(task_id, text, voice, model, kwargs)
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/v1/voice/cosyvoice/async/<task_id>", methods=["GET"])
def query_cosyvoice_task(task_id):
    data = redis_client.get(f"cosyvoice_task:{task_id}")
    if not data:
        return jsonify({"error": "Task not found"}), 404
        
    return jsonify(json.loads(data))


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



def process_fish_audio_task(task_id: str, text: str, reference_id: str = None, 
                            reference_audio: str = None, speed: float = 1.0, 
                            volume: int = 0, output_format: str = "mp3", 
                            latency: str = "normal"):
    """
    Process Fish Audio TTS task in background.
    """
    try:
        api_key = os.getenv("FISH_API_KEY")
        if not api_key:
             raise ValueError("FISH_API_KEY environment variable is not set")

        client = FishAudio(api_key=api_key)

        config_kwargs = {
            "prosody": Prosody(speed=speed, volume=volume),
            "format": output_format,
            "latency": latency,
        }

        if reference_id:
            config_kwargs["reference_id"] = reference_id
        elif reference_audio:
            if os.path.exists(reference_audio):
                with open(reference_audio, "rb") as f:
                    config_kwargs["reference_audio"] = f.read()
            else:
                 raise ValueError(f"Reference audio file not found: {reference_audio}")
        
        config = TTSConfig(**config_kwargs)
        
        # Audio comes back as an iterator or bytes depending on SDK version.
        # Based on user script: audio = client.tts.convert(text=text, config=config)
        # and save(audio, ...) where save consumes it.
        # We need bytes.
        
        audio_generator = client.tts.convert(text=text, config=config)
        audio_content = b"".join(audio_generator)

        voice_b64 = base64.b64encode(audio_content).decode("ascii")

        task_info = {
            "status": "success",
            "voice_b64": voice_b64,
            "created_at": time.time(),
            "task_id": task_id,
        }
    except Exception as e:
        task_info = {
            "status": "failed",
            "error": str(e),
            "created_at": time.time(),
            "task_id": task_id
        }

    redis_client.setex(f"fishaudio_task:{task_id}", REDIS_TTL, json.dumps(task_info))


@app.route("/v1/voice/fish-audio/text-to-speech", methods=["POST"])
def fish_audio_tts_endpoint():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    
    if not text:
        return jsonify({"error": "parameter 'text' is required"}), 400

    reference_id = payload.get("reference_id")
    reference_audio = payload.get("reference_audio")
    speed = float(payload.get("speed", 1.0))
    volume = int(payload.get("volume", 0))
    output_format = payload.get("format", "mp3")
    latency = payload.get("latency", "normal")

    task_id = str(uuid.uuid4())
    
    task_info = {
        "status": "processing",
        "created_at": time.time(),
        "task_id": task_id
    }
    redis_client.setex(f"fishaudio_task:{task_id}", REDIS_TTL, json.dumps(task_info))

    thread = threading.Thread(
        target=process_fish_audio_task,
        args=(task_id, text, reference_id, reference_audio, speed, volume, output_format, latency)
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/v1/voice/fish-audio/text-to-speech/<task_id>", methods=["GET"])
def query_fish_audio_task(task_id):
    data = redis_client.get(f"fishaudio_task:{task_id}")
    if not data:
        return jsonify({"error": "Task not found"}), 404
        
    return jsonify(json.loads(data))


IMAGE_TASK_PREFIX = "image_generation_task:"


def _parse_image_generation_payload(payload):
    provider = (payload.get("provider") or "").strip().lower()
    model = (payload.get("model") or "").strip()
    prompt = (payload.get("prompt") or "").strip()
    size = payload.get("size")
    aspect_ratio = payload.get("aspect_ratio")
    resolution = payload.get("resolution")
    images = payload.get("images")
    return_url = payload.get("return_url", False)
    record_history = payload.get("record_history", False)

    if not provider:
        raise ValueError("parameter 'provider' is required")
    if not model:
        raise ValueError("parameter 'model' is required")
    if not prompt:
        raise ValueError("parameter 'prompt' is required")
    if size is not None and (not isinstance(size, str) or not size.strip()):
        raise ValueError("parameter 'size' must be a non-empty string")
    if aspect_ratio is not None and (
        not isinstance(aspect_ratio, str) or not aspect_ratio.strip()
    ):
        raise ValueError("parameter 'aspect_ratio' must be a non-empty string")
    if resolution is not None and (
        not isinstance(resolution, str) or not resolution.strip()
    ):
        raise ValueError("parameter 'resolution' must be a non-empty string")
    if images is not None and not isinstance(images, list):
        raise ValueError("parameter 'images' must be an array of image references")
    try:
        images = normalize_input_images(images, provider=provider, model=model)
    except ImageGenerationError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(return_url, bool):
        raise ValueError("parameter 'return_url' must be a boolean")
    if not isinstance(record_history, bool):
        raise ValueError("parameter 'record_history' must be a boolean")
    try:
        size, aspect_ratio = normalize_image_aspect_ratio(
            provider,
            model,
            size.strip() if size else None,
            aspect_ratio.strip() if aspect_ratio else None,
        )
    except ImageGenerationError as exc:
        raise ValueError(str(exc)) from exc
    return (
        provider,
        model,
        prompt,
        size.strip() if size else None,
        images,
        return_url,
        aspect_ratio.strip() if aspect_ratio else None,
        resolution.strip() if resolution else None,
        record_history,
    )


def _generate_image_response(
    provider,
    model,
    prompt,
    size,
    images,
    return_url,
    aspect_ratio=None,
    resolution=None,
    generation_id=None,
    record_history=False,
):
    # Validate storage before generating a billable image.
    storage = S3ImageStorage.from_env() if return_url else None
    image = generate_normalized_image(
        provider=provider,
        model=model,
        prompt=prompt,
        size=size,
        images=images,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )
    result = {
        "provider": provider,
        "model": model,
    }
    if storage:
        result["image_url"] = storage.upload_image(image)
    else:
        result["image_base64"] = base64.b64encode(image).decode("ascii")
    if record_history:
        save_image_history_async(
            generation_id=generation_id or str(uuid.uuid4()),
            image=image,
            provider=provider,
            model=model,
            prompt=prompt,
            size=_image_history_size(size, aspect_ratio, resolution),
        )
    return result


def _image_history_size(
    size: str | None,
    aspect_ratio: str | None,
    resolution: str | None,
) -> str | None:
    if aspect_ratio and resolution:
        return f"{aspect_ratio} @ {resolution}"
    return aspect_ratio or resolution or size


@app.route("/v1/images/models", methods=["GET"])
def image_models_endpoint():
    """Return the versioned catalog of supported image providers and models."""
    return jsonify(get_image_model_catalog())


@app.route("/v1/images/generations", methods=["POST"])
def image_generation_endpoint():
    """Generate one image and wait for the provider to return the final result."""
    try:
        params = _parse_image_generation_payload(request.get_json(silent=True) or {})
        return jsonify(
            _generate_image_response(
                *params[:8],
                record_history=params[8],
            )
        )
    except RequestEntityTooLarge as exc:
        return request_too_large(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except (ImageGenerationError, ObjectStorageError) as exc:
        app.logger.warning(
            "Image generation dependency failed provider=%s model=%s: %s",
            params[0] if "params" in locals() else "unknown",
            params[1] if "params" in locals() else "unknown",
            exc,
        )
        # Cloudflare replaces many origin 502 response bodies with a generic
        # plain-text page. 424 preserves the structured provider error while
        # still identifying the failure as an upstream dependency problem.
        return jsonify({"error": str(exc)}), 424
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def process_image_generation_task(
    task_id,
    provider,
    model,
    prompt,
    size,
    images,
    return_url,
    aspect_ratio=None,
    resolution=None,
    record_history=False,
):
    try:
        task_info = {
            "status": "success",
            **_generate_image_response(
                provider=provider,
                model=model,
                prompt=prompt,
                size=size,
                images=images,
                return_url=return_url,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                generation_id=task_id,
                record_history=record_history,
            ),
            "created_at": time.time(),
            "task_id": task_id,
        }
    except Exception as exc:
        app.logger.exception(
            "Image generation task failed task_id=%s provider=%s model=%s",
            task_id,
            provider,
            model,
        )
        task_info = {
            "status": "failed",
            "error": str(exc),
            "provider": provider,
            "model": model,
            "created_at": time.time(),
            "task_id": task_id,
        }
    redis_client.setex(f"{IMAGE_TASK_PREFIX}{task_id}", REDIS_TTL, json.dumps(task_info))


@app.route("/v1/images/generations/async", methods=["POST"])
def async_image_generation_endpoint():
    """Create a local image generation task and return immediately."""
    try:
        (
            provider,
            model,
            prompt,
            size,
            images,
            return_url,
            aspect_ratio,
            resolution,
            record_history,
        ) = _parse_image_generation_payload(request.get_json(silent=True) or {})
        # Validate provider and storage configuration before accepting a task.
        get_provider(provider)
        if return_url:
            S3ImageStorage.from_env()
    except RequestEntityTooLarge as exc:
        return request_too_large(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except (ImageGenerationError, ObjectStorageError) as exc:
        return jsonify({"error": str(exc)}), 500

    task_id = str(uuid.uuid4())
    task_info = {
        "status": "processing",
        "provider": provider,
        "model": model,
        "return_url": return_url,
        "created_at": time.time(),
        "task_id": task_id,
    }
    redis_client.setex(f"{IMAGE_TASK_PREFIX}{task_id}", REDIS_TTL, json.dumps(task_info))
    thread = threading.Thread(
        target=process_image_generation_task,
        args=(
            task_id,
            provider,
            model,
            prompt,
            size,
            images,
            return_url,
            aspect_ratio,
            resolution,
            record_history,
        ),
        daemon=True,
    )
    thread.start()
    return jsonify({"task_id": task_id}), 202


@app.route("/v1/images/generations/async/<task_id>", methods=["GET"])
def query_image_generation_task(task_id):
    data = redis_client.get(f"{IMAGE_TASK_PREFIX}{task_id}")
    if not data:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(json.loads(data))


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


DEFAULT_WECHAT_THEME = "professional-clean"
_wechat_appid = os.getenv("WECHAT_APPID")
_wechat_secret = os.getenv("WECHAT_SECRET")


def _wechat_credentials(payload=None):
    """Resolve credentials without requiring secrets in URL query strings."""
    payload = payload if isinstance(payload, dict) else {}
    appid = (
        payload.get("appid")
        or request.headers.get("X-WeChat-AppId")
        or _wechat_appid
    )
    secret = (
        payload.get("secret")
        or request.headers.get("X-WeChat-AppSecret")
        or _wechat_secret
    )
    if not appid or not secret:
        raise ValueError(
            "'appid' and 'secret' are required (or set WECHAT_APPID/WECHAT_SECRET)"
        )
    return appid, secret


def _wechat_token(payload=None):
    appid, secret = _wechat_credentials(payload)
    return get_access_token(appid, secret)


def _parse_wechat_int(value, name, minimum, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"parameter '{name}' must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"parameter '{name}' must be an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        expected = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"parameter '{name}' must be {expected}")
    return parsed


def _wechat_draft_error_response(exc):
    if exc.errcode == 40007:
        status = 404
    elif exc.errcode in {40114, 41039, 45166, 53404, 53405, 53406, 88000}:
        status = 400
    else:
        status = 502
    return jsonify(
        {
            "error": exc.errmsg,
            "wechat_errcode": exc.errcode,
            "operation": exc.operation,
        }
    ), status


def _convert_markdown(markdown_text, theme_name):
    """Convert Markdown to WeChat inline-style HTML using the named theme.

    Returns (converter_result, theme). Raises ValueError/FileNotFoundError on
    a bad theme name.
    """
    theme = load_theme(theme_name)
    converter = WeChatConverter(theme=theme)
    result = converter.convert(markdown_text)
    return result, theme


@app.route("/v1/wechat/markdown/themes", methods=["GET"])
def wechat_themes_endpoint():
    """List the排版主题 (layout themes) available to the markdown converter."""
    names = list_themes()
    themes = []
    for name in names:
        try:
            theme = load_theme(name)
            themes.append({"name": name, "description": theme.description})
        except Exception:
            themes.append({"name": name, "description": ""})
    return jsonify({"themes": themes})


@app.route("/v1/wechat/markdown/preview", methods=["POST"])
def wechat_preview_endpoint():
    """Render Markdown to WeChat-compatible HTML for preview.

    Payload:
      markdown  (str, required)  Markdown source
      theme     (str, optional)  theme name, default "professional-clean"
      title     (str, optional)  override the extracted H1 title
      full_page (bool, optional) wrap body HTML in a full HTML document
                                 (browser preview only, not for WeChat)
    Response: {html, title, digest, images, theme}
    """
    payload = request.get_json(silent=True) or {}
    markdown_text = (payload.get("markdown") or "").strip()
    theme_name = payload.get("theme") or DEFAULT_WECHAT_THEME
    full_page = bool(payload.get("full_page") or False)

    if not markdown_text:
        return jsonify({"error": "parameter 'markdown' is required"}), 400

    try:
        result, theme = _convert_markdown(markdown_text, theme_name)
    except FileNotFoundError:
        return jsonify({"error": f"theme not found: {theme_name}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    html = preview_html(result.html, theme) if full_page else result.html
    title = payload.get("title") or result.title

    return jsonify(
        {
            "html": html,
            "title": title,
            "digest": result.digest,
            "images": result.images,
            "theme": theme_name,
        }
    )


@app.route("/v1/wechat/markdown/draft", methods=["POST"])
def wechat_draft_endpoint():
    """Convert Markdown and push it into the WeChat Official Account draft box.

    Payload:
      markdown  (str, required)   Markdown source
      appid     (str, optional)   WeChat AppID   (falls back to WECHAT_APPID env)
      secret    (str, optional)   WeChat AppSecret (falls back to WECHAT_SECRET env)
      theme     (str, optional)   theme name, default "professional-clean"
      title     (str, optional)   override the article title
      author    (str, optional)   article author
      digest    (str, optional)   override the summary (<=120 UTF-8 bytes)
      cover     (str, optional)   cover image as URL / data URI / base64;
                                  uploaded as the article thumbnail
      content_source_url (str, optional) "阅读原文" link
    Response: {media_id, title, digest, theme, images_uploaded}
    """
    payload = request.get_json(silent=True) or {}
    markdown_text = (payload.get("markdown") or "").strip()
    theme_name = payload.get("theme") or DEFAULT_WECHAT_THEME
    appid = payload.get("appid") or _wechat_appid
    secret = payload.get("secret") or _wechat_secret

    if not markdown_text:
        return jsonify({"error": "parameter 'markdown' is required"}), 400
    if not appid or not secret:
        return jsonify({"error": "'appid' and 'secret' are required (or set WECHAT_APPID/WECHAT_SECRET)"}), 400

    try:
        result, _theme = _convert_markdown(markdown_text, theme_name)
    except FileNotFoundError:
        return jsonify({"error": f"theme not found: {theme_name}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    try:
        token = get_access_token(appid, secret)

        # Upload inline images and rewrite their src so the article references
        # WeChat-hosted URLs (external/base64 images are otherwise blocked).
        # Rewriting happens on the parsed DOM (not string replacement) so URLs
        # with '&' and prefix-colliding URLs are handled correctly, and each
        # distinct src is uploaded at most once.
        def _resolve_image(src):
            if src.startswith("#"):
                return None  # in-page anchor, not an image reference
            # Protocol-relative URLs (//host/path) are real external images
            # WeChat blocks; normalize so they get uploaded like any http(s) src.
            resolve_src = "https:" + src if src.startswith("//") else src
            try:
                data, filename = load_image_bytes(resolve_src)
            except Exception:
                # Not a resolvable/uploadable image (relative path, non-image
                # data, unreachable URL) — leave the original src untouched.
                return None
            return upload_image_bytes(token, data, filename)

        html, images_uploaded = rewrite_image_srcs(result.html, _resolve_image)

        # Upload cover image as permanent material (article thumbnail).
        thumb_media_id = None
        cover = payload.get("cover")
        if cover:
            try:
                cover_data, cover_name = load_image_bytes(cover)
            except Exception as exc:
                return jsonify({"error": f"invalid cover image: {exc}"}), 400
            thumb_media_id = upload_thumb_bytes(token, cover_data, cover_name)

        title = payload.get("title") or result.title or "无标题"
        digest = payload.get("digest") or result.digest

        draft = create_draft(
            access_token=token,
            title=title,
            html=html,
            digest=digest,
            thumb_media_id=thumb_media_id,
            author=payload.get("author"),
            content_source_url=payload.get("content_source_url"),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "media_id": draft.media_id,
            "title": title,
            "digest": digest,
            "theme": theme_name,
            "images_uploaded": images_uploaded,
        }
    )


@app.route("/v1/wechat/drafts", methods=["GET"])
def wechat_drafts_endpoint():
    """List drafts. Query: offset=0, count=10, no_content=0."""
    try:
        offset = _parse_wechat_int(request.args.get("offset", 0), "offset", 0)
        count = _parse_wechat_int(request.args.get("count", 10), "count", 1, 20)
        no_content = _parse_wechat_int(
            request.args.get("no_content", 0), "no_content", 0, 1
        )
        data = list_drafts(_wechat_token(), offset, count, no_content)
        return jsonify(data)
    except WeChatDraftAPIError as exc:
        return _wechat_draft_error_response(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/v1/wechat/drafts/<path:media_id>", methods=["GET"])
def wechat_draft_detail_endpoint(media_id):
    """Get a draft by media ID."""
    media_id = media_id.strip()
    if not media_id:
        return jsonify({"error": "parameter 'media_id' is required"}), 400
    try:
        return jsonify(get_draft(_wechat_token(), media_id))
    except WeChatDraftAPIError as exc:
        return _wechat_draft_error_response(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/v1/wechat/drafts/<path:media_id>", methods=["PUT"])
def wechat_draft_update_endpoint(media_id):
    """Update one article in a draft, repairing a missing cover media ID."""
    media_id = media_id.strip()
    if not media_id:
        return jsonify({"error": "parameter 'media_id' is required"}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    article = payload.get("article")
    if not isinstance(article, dict):
        return jsonify({"error": "parameter 'article' is required and must be an object"}), 400
    for field in ("title", "content"):
        if not isinstance(article.get(field), str) or not article[field].strip():
            return jsonify({"error": f"parameter 'article.{field}' is required"}), 400
    for field in ("need_open_comment", "only_fans_can_comment"):
        value = article.get(field)
        if field in article and (isinstance(value, bool) or value not in (0, 1)):
            return jsonify({"error": f"parameter 'article.{field}' must be 0 or 1"}), 400

    try:
        index = _parse_wechat_int(payload.get("index", 0), "index", 0)
        token = _wechat_token(payload)
        article = article.copy()

        # Draft detail responses contain read-only URLs that the update API does
        # not accept. Keep thumb_url only as a possible source for repairing an
        # empty permanent thumbnail media ID.
        cover = payload.get("cover") or article.pop("thumb_url", None)
        article.pop("url", None)

        cover_reuploaded = False
        article_type = article.get("article_type") or "news"
        if article_type == "news" and not article.get("thumb_media_id"):
            if not cover:
                current = get_draft(token, media_id)
                news_items = current.get("news_item") or []
                if index >= len(news_items):
                    raise ValueError(
                        f"parameter 'index' is out of range for draft with {len(news_items)} articles"
                    )
                cover = news_items[index].get("thumb_url")

            if not cover:
                raise ValueError(
                    "article.thumb_media_id is required for news articles and no cover image is available"
                )

            cover_data, cover_name = load_image_bytes(cover)
            article["thumb_media_id"] = upload_thumb_bytes(
                token, cover_data, cover_name
            )
            cover_reuploaded = True

        update_draft(token, media_id, index, article)
        response = {"media_id": media_id, "index": index, "updated": True}
        if cover_reuploaded:
            response.update(
                {
                    "cover_reuploaded": True,
                    "thumb_media_id": article["thumb_media_id"],
                }
            )
        return jsonify(response)
    except WeChatDraftAPIError as exc:
        return _wechat_draft_error_response(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/v1/wechat/drafts/<path:media_id>", methods=["DELETE"])
def wechat_draft_delete_endpoint(media_id):
    """Permanently delete a draft by media ID."""
    media_id = media_id.strip()
    if not media_id:
        return jsonify({"error": "parameter 'media_id' is required"}), 400
    try:
        delete_draft(_wechat_token(), media_id)
        return jsonify({"media_id": media_id, "deleted": True})
    except WeChatDraftAPIError as exc:
        return _wechat_draft_error_response(exc)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


def create_app() -> Flask:
    """Flask factory for WSGI/ASGI servers."""
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
