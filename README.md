# AI Media Services API

A Flask-based HTTP API service that integrates multiple AI media generation capabilities:
- **Alibaba Cloud DashScope CosyVoice TTS**: High-quality text-to-speech synthesis.
- **Volcano Engine Podcast TTS** ([Official Docs](https://www.volcengine.com/docs/6561/1668014?lang=zh)): Multi-speaker, conversational podcast generation with music support.
- **Image Stitching**: Utility to stitch multiple images vertically or horizontally.

This service exposes these capabilities via simple RESTful endpoints, returning base64-encoded results.

## Requirements
- Python 3.11+
- DashScope API key: set `DASHSCOPE_API_KEY`
- Optional: Docker

## Environment Variables
The following environment variables are required to run the service:

| Variable | Description | Required | 
| :--- | :--- | :--- |
| `DASHSCOPE_API_KEY` | Alibaba Cloud DashScope API Key (for CosyVoice) | Yes |
| `VOLC_APPID` | Volcano Engine App ID (for Podcast TTS) | Yes (for Podcast) |
| `VOLC_ACCESS_TOKEN` | Volcano Engine Access Token (for Podcast TTS) | Yes (for Podcast) |

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



- **POST** `/v1/voice/podcast`
  > Official Documentation: [Volcano Engine Podcast TTS](https://www.volcengine.com/docs/6561/1668014?lang=zh)
- Body (JSON):
  ```json
  {
      "scripts": [
          {
              "speaker": "zh_male_dayixiansheng_v2_saturn_bigtts",
              "text": "今天呢我们要聊的呢是火山引擎在这个 FORCE 原动力大会上面的一些比较重磅的发布。"
          },
          {
              "speaker": "zh_female_mizaitongxue_v2_saturn_bigtts",
              "text": "来看看都有哪些亮点哈。"
          }
      ]
  }
  ```
  - `scripts` (required): List of script objects containing `speaker` and `text`
  - `use_head_music` (optional): Boolean, default `false`
  - `use_tail_music` (optional): Boolean, default `false`
  
  **Available Speakers**:
  
  > 💡 Note: Speakers from the same series work best together. Default series is `dayi/mizai`. 
  
  | Series | Speaker ID |
  | :--- | :--- |
  | **Black Cat Detective Agency Mizai** | `zh_female_mizaitongxue_v2_saturn_bigtts` |
  | | `zh_male_dayixiansheng_v2_saturn_bigtts` |
  | **Liu Fei and Xiaolei** | `zh_male_liufei_v2_saturn_bigtts` |
  | | `zh_male_xiaolei_v2_saturn_bigtts` |
- Response:
  ```json
  {
    "voice_b64": "<base64 audio>"
  }
  ```
- Environment Variables Required:
  - `VOLC_APPID`
  - `VOLC_ACCESS_TOKEN`

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
