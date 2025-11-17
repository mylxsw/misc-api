# coding=utf-8
"""Expose CosyVoice TTS as an HTTP API.

POST /v1/voice/cosyvoice
Payload: {"text": "要转换的文本", "voice": "音色(可选)"}
Response: {"voice_b64": "base64编码的音频数据", "request_id": "...", "first_package_delay_ms": 123}
"""
import base64
import os
from typing import Tuple

from flask import Flask, jsonify, request
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer

# Resolve and validate the dashscope API key early so we can return a clearer error
# instead of a TypeError from the SDK when it tries to concat None.
_resolved_api_key = os.getenv("DASHSCOPE_API_KEY") or dashscope.api_key
if not _resolved_api_key:
    raise RuntimeError("DASHSCOPE_API_KEY is not set; please export it for the service to start.")
dashscope.api_key = _resolved_api_key

app = Flask(__name__)

DEFAULT_MODEL = "cosyvoice-v2"
DEFAULT_VOICE = "libai_v2"


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


def create_app() -> Flask:
    """Flask factory for WSGI/ASGI servers."""
    return app


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
