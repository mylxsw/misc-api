# AI Media Services API

A Flask-based HTTP API service that integrates multiple AI media generation capabilities:
- **Alibaba Cloud DashScope CosyVoice TTS**: High-quality text-to-speech synthesis.
- **Volcano Engine Podcast TTS** ([Official Docs](https://www.volcengine.com/docs/6561/1668014?lang=zh)): Multi-speaker, conversational podcast generation with music support.
- **Fish Audio TTS**: Text-to-speech synthesis using Fish Audio SDK.
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
| `FISH_API_KEY` | Fish Audio API Key | Yes (for Fish Audio) |
| `WECHAT_APPID` | WeChat Official Account AppID (for draft push) | No (or pass in request body) |
| `WECHAT_SECRET` | WeChat Official Account AppSecret (for draft push) | No (or pass in request body) |

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

## API 详情

### API 概览

- [`POST /v1/voice/cosyvoice`](#cosyvoice-text-to-speech)：使用阿里云 DashScope CosyVoice 将文本同步转换为语音。
- [`POST /v1/voice/podcast`](#create-podcast-task)：创建火山引擎多人对话播客生成任务。
- [`GET /v1/voice/podcast/<task_id>`](#get-podcast-task)：查询播客生成任务的状态和结果。
- [`POST /v1/voice/fish-audio/text-to-speech`](#create-fish-audio-task)：创建 Fish Audio 文本转语音任务。
- [`GET /v1/voice/fish-audio/text-to-speech/<task_id>`](#get-fish-audio-task)：查询 Fish Audio 任务的状态和结果。
- [`GET /v1/wechat/markdown/themes`](#list-wechat-themes)：获取微信公众号 Markdown 排版支持的主题。
- [`POST /v1/wechat/markdown/preview`](#preview-wechat-article)：将 Markdown 转换为可预览或粘贴到微信编辑器的 HTML。
- [`POST /v1/wechat/markdown/draft`](#publish-wechat-draft)：将 Markdown 文章及图片上传到微信公众号草稿箱。

### CosyVoice text-to-speech

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



### Create podcast task

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
    "task_id": "uuid-string"
  }
  ```

### Get podcast task

- **GET** `/v1/voice/podcast/<task_id>`
- Response:
  - Processing:
    ```json
    {
      "status": "processing",
      "created_at": 1700000000.0,
      "task_id": "..."
    }
    ```
  - Success:
    ```json
    {
      "status": "success",
      "voice_b64": "<base64 audio>",
      "created_at": ...,
      "task_id": "..."
    }
    ```
  - Failed:
    ```json
    {
      "status": "failed",
      "error": "error message",
      "created_at": ...,
      "task_id": "..."
    }
    ```
  > Note: Task results are stored for 7 days.

- Environment Variables Required:
  - `VOLC_APPID`
  - `VOLC_ACCESS_TOKEN`
  - `REDIS_URL` (default: `redis://localhost:6379/0`)

### Create Fish Audio task

- **POST** `/v1/voice/fish-audio/text-to-speech`
- Body (JSON):
  ```json
  {
      "text": "Hello, world!",
      "reference_id": "optional_voice_id",
      "speed": 1.0,
      "volume": 0,
      "format": "mp3"
  }
  ```
  - `text` (required): Text to convert
  - `reference_id` (optional): Reference ID for platform voice
  - `reference_audio` (optional): Path to local reference audio file (server-side)
  - `speed` (optional): Float, default 1.0
  - `volume` (optional): Int, default 0
  - `format` (optional): "mp3" (default), "wav", "pcm", "opus"
  - `latency` (optional): "normal" (default), "balanced"
- Response:
  ```json
  {
      "task_id": "uuid-string"
  }
  ```

### Get Fish Audio task

- **GET** `/v1/voice/fish-audio/text-to-speech/<task_id>`
- Response:
  - Processing:
    ```json
    {
      "status": "processing",
      "created_at": 1700000000.0,
      "task_id": "..."
    }
    ```
  - Success:
    ```json
    {
      "status": "success",
      "voice_b64": "<base64 audio>",
      "created_at": ...,
      "task_id": "..."
    }
    ```
  - Failed:
    ```json
    {
      "status": "failed",
      "error": "error message",
      "created_at": ...,
      "task_id": "..."
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

### Markdown → WeChat (公众号排版与草稿推送)

Convert Markdown into WeChat-compatible inline-style HTML (18 built-in themes,
CJK spacing fixes, dark-mode attributes, list/link/code-block handling), then
either preview it or push it straight into the Official Account draft box.

#### List WeChat themes
- **GET** `/v1/wechat/markdown/themes`
- Response: `{"themes": [{"name": "professional-clean", "description": "..."}, ...]}`

#### Preview WeChat article
Renders Markdown to HTML for pasting into the WeChat editor or previewing in a
browser. No WeChat credentials needed.

- **POST** `/v1/wechat/markdown/preview`
- Body (JSON):

  | Field | Type | Required | Description |
  | :--- | :--- | :--- | :--- |
  | `markdown` | string | Yes | Markdown source |
  | `theme` | string | No | Theme name (default `professional-clean`) |
  | `title` | string | No | Override the extracted H1 title |
  | `full_page` | bool | No | Wrap body HTML in a full HTML document (browser preview only) |

- Response:
  ```json
  {
    "html": "<p ...>...</p>",
    "title": "标题",
    "digest": "自动摘要...",
    "images": ["https://..."],
    "theme": "professional-clean"
  }
  ```

#### Publish WeChat draft
Converts the Markdown, uploads inline images (URL / base64) and an optional
cover to WeChat, then creates a draft. `appid`/`secret` may be passed in the
body or supplied via `WECHAT_APPID` / `WECHAT_SECRET` env vars.

- **POST** `/v1/wechat/markdown/draft`
- Body (JSON):

  | Field | Type | Required | Description |
  | :--- | :--- | :--- | :--- |
  | `markdown` | string | Yes | Markdown source |
  | `appid` | string | No* | WeChat AppID (falls back to `WECHAT_APPID`) |
  | `secret` | string | No* | WeChat AppSecret (falls back to `WECHAT_SECRET`) |
  | `theme` | string | No | Theme name (default `professional-clean`) |
  | `title` | string | No | Override the article title |
  | `author` | string | No | Article author |
  | `digest` | string | No | Override the summary (≤120 UTF-8 bytes) |
  | `cover` | string | No | Cover image as URL / data URI / base64 (uploaded as thumbnail) |
  | `content_source_url` | string | No | "阅读原文" link |

  *At least one of the request field or the corresponding env var must be set.

- Response:
  ```json
  {
    "media_id": "draft-media-id",
    "title": "标题",
    "digest": "摘要...",
    "theme": "professional-clean",
    "images_uploaded": 2
  }
  ```

Sample request:
```bash
curl -X POST http://localhost:8000/v1/wechat/markdown/preview \
  -H "Content-Type: application/json" \
  -d '{"markdown":"# 标题\n\n正文内容 with English 混排。","theme":"sspai"}'
```

## Project files
- `server.py`: Flask app exposing the TTS endpoint
- `lib/wechat/`: Markdown → WeChat HTML converter, themes, and draft-box publisher (ported from wewrite)
- `Dockerfile`: uv-based container image using Gunicorn
- `pyproject.toml`: dependencies (managed by uv)
- `LICENSE`: MIT

## License
MIT
