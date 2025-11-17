# CosyVoice TTS API

Flask-based HTTP API that wraps Alibaba Cloud DashScope CosyVoice TTS. It accepts text and returns base64-encoded audio.

## Requirements
- Python 3.11+
- DashScope API key: set `DASHSCOPE_API_KEY`
- Optional: Docker

## Quick start (local)
```bash
# Install deps with uv (creates .venv)
uv sync --no-dev

# Run the API
uv run python server.py
```
The service listens on `http://localhost:8000`.

## Docker
```bash
docker build -t cosyvoice-api .
docker run -p 8000:8000 -e DASHSCOPE_API_KEY=your_key cosyvoice-api
```

## API
- **POST** `/v1/voice/cosyvoice`
- Body (JSON):
  ```json
  { "text": "要转换的文本", "voice": "libai_v2" }
  ```
  - `text` (required): text to synthesize
  - `voice` (optional): CosyVoice voice id, defaults to `libai_v2`
- Response:
  ```json
  {
    "voice_b64": "<base64 audio>",
    "request_id": "...",
    "first_package_delay_ms": 123
  }
  ```

Sample request:
```bash
curl -X POST http://localhost:8000/v1/voice/cosyvoice \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，世界","voice":"libai_v2"}' \
  | python - <<'PY'
import sys, json, base64
r = json.load(sys.stdin)
with open("output.wav", "wb") as f:
    f.write(base64.b64decode(r["voice_b64"]))
print("Saved to output.wav")
PY
```

## Project files
- `server.py`: Flask app exposing the TTS endpoint
- `Dockerfile`: uv-based container image using Gunicorn
- `pyproject.toml`: dependencies (managed by uv)
- `LICENSE`: MIT

## License
MIT
