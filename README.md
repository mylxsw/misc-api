# AI Media Services API

A Flask-based HTTP API service that integrates multiple AI media generation capabilities:
- **Alibaba Cloud DashScope CosyVoice TTS**: High-quality text-to-speech synthesis.
- **Volcano Engine Podcast TTS** ([Official Docs](https://www.volcengine.com/docs/6561/1668014?lang=zh)): Multi-speaker, conversational podcast generation with music support.
- **Fish Audio TTS**: Text-to-speech synthesis using Fish Audio SDK.
- **Unified Image Generation**: Synchronous and asynchronous image generation through APIMart, ToAPIs, Google Gemini, or xAI.
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
| `DASHSCOPE_API_KEY` | Alibaba Cloud DashScope API Key (for CosyVoice and `aliyun` image generation) | Yes (for Alibaba Cloud APIs) |
| `ALIYUN_API_BASE` | Alibaba Cloud Model Studio API base; use the base for the same region/workspace as the API Key (default `https://dashscope.aliyuncs.com/api/v1`) | No |
| `VOLC_APPID` | Volcano Engine App ID (for Podcast TTS) | Yes (for Podcast) |
| `VOLC_ACCESS_TOKEN` | Volcano Engine Access Token (for Podcast TTS) | Yes (for Podcast) |
| `FISH_API_KEY` | Fish Audio API Key | Yes (for Fish Audio) |
| `ARK_API_KEY` | Volcano Engine Ark API Key | Yes (for Ark image generation) |
| `ARK_API_BASE` | Ark API base URL (default `https://ark.cn-beijing.volces.com/api/v3`) | No |
| `APIMART_API_KEY` | APIMart API Key | Yes (for APIMart image generation) |
| `APIMART_API_BASE` | APIMart API base URL (default `https://api.apimart.ai/v1`) | No |
| `TOAPIS_API_KEY` | ToAPIs API Key | Yes (for ToAPIs image generation) |
| `TOAPIS_API_BASE` | ToAPIs API base URL (default `https://toapis.com/v1`) | No |
| `GEMINI_API_KEY` | Google Gemini API Key | Yes (for Gemini image generation) |
| `GEMINI_API_BASE` | Gemini API base URL | No |
| `X_AI_API_KEY` | xAI API Key | Yes (for xAI image generation) |
| `X_AI_API_BASE` | xAI API base URL | No |
| `IMAGE_GENERATION_MAX_WAIT` | Maximum provider wait time in seconds (default `300`) | No |
| `IMAGE_GENERATION_POLL_INTERVAL` | APIMart/ToAPIs/Alibaba Cloud polling interval in seconds (default `5`) | No |
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

- [`GET /v1/images/models`](#list-image-providers-and-models)：获取支持的图片 provider 与模型目录。
- [`POST /v1/images/generations`](#generate-image-synchronously)：通过指定 provider 和 model 同步生成一张图片。
- [`POST /v1/images/generations/async`](#create-image-generation-task)：创建统一图片生成异步任务。
- [`GET /v1/images/generations/async/<task_id>`](#get-image-generation-task)：查询图片生成任务状态和 Base64 结果。
- [`POST /v1/voice/cosyvoice`](#cosyvoice-text-to-speech)：使用阿里云 DashScope CosyVoice 将文本同步转换为语音。
- [`POST /v1/voice/podcast`](#create-podcast-task)：创建火山引擎多人对话播客生成任务。
- [`GET /v1/voice/podcast/<task_id>`](#get-podcast-task)：查询播客生成任务的状态和结果。
- [`POST /v1/voice/fish-audio/text-to-speech`](#create-fish-audio-task)：创建 Fish Audio 文本转语音任务。
- [`GET /v1/voice/fish-audio/text-to-speech/<task_id>`](#get-fish-audio-task)：查询 Fish Audio 任务的状态和结果。
- [`GET /v1/wechat/markdown/themes`](#list-wechat-themes)：获取微信公众号 Markdown 排版支持的主题。
- [`POST /v1/wechat/markdown/preview`](#preview-wechat-article)：将 Markdown 转换为可预览或粘贴到微信编辑器的 HTML。
- [`POST /v1/wechat/markdown/draft`](#publish-wechat-draft)：将 Markdown 文章及图片上传到微信公众号草稿箱。

### List image providers and models

- **GET** `/v1/images/models`
- Returns the built-in image provider/model catalog. `updated_at` identifies the
  catalog snapshot date; the current data is updated through `2026-08-31`.
- Response:
  ```json
  {
    "updated_at": "2026-08-31",
    "providers": [
      {
        "provider": "apimart",
        "models": ["gpt-image-2", "gemini-3.1-flash-image-preview"]
      },
      {
        "provider": "ark",
        "models": ["doubao-seedream-5-0-pro", "doubao-seedream-4-0-250828"],
        "note": "Ark Endpoint IDs are also accepted and may be used instead of public Model IDs."
      }
    ]
  }
  ```

### Generate image synchronously

- **POST** `/v1/images/generations`
- Body (JSON):

  | Field | Type | Required | Description |
  | :--- | :--- | :--- | :--- |
  | `provider` | string | Yes | `aliyun`, `ark`, `apimart`, `toapis`, `gemini`, or `xai` |
  | `model` | string | Yes | Provider-specific model identifier |
  | `prompt` | string | Yes | Image description |
  | `size` | string | No | Resolution (`1K`, `2K`, `4K`) or aspect ratio (`1:1`, `16:9`, etc.) |

  ```json
  {
    "provider": "apimart",
    "model": "gpt-image-2",
    "prompt": "一只橘猫坐在窗台上看夕阳，水彩画风格",
    "size": "16:9"
  }
  ```

  APIMart and ToAPIs are asynchronous upstream services. Alibaba Cloud Wan
  models are asynchronous, while Qwen Image and Z-Image are synchronous. This
  endpoint hides these differences, waits when necessary, downloads the final
  image, and returns the same response format for every provider. Ark requests
  `b64_json` directly from Seedream and therefore does not need a result download.

- Response:
  ```json
  {
    "image_base64": "<base64 image>",
    "provider": "apimart",
    "model": "gpt-image-2"
  }
  ```

#### Supported providers and models

The router accepts provider-specific model identifiers, making it possible to
add models without changing the public request format. The initially supported
and documented combinations are:

| Provider | Models |
| :--- | :--- |
| `aliyun` | `qwen-image-3.0-pro`, `qwen-image-3.0`, `wan2.7-image-pro`, other `wan*` image models, and `z-image-turbo` |
| `ark` | Ark Model IDs or Endpoint IDs for Doubao Seedream 5.0 Pro/Lite, Seedream 4.5, and Seedream 4.0 |
| `apimart` | `gpt-image-2`, `gemini-3.1-flash-image-preview`, `gemini-3-pro-image-preview` and their APIMart aliases |
| `toapis` | `gpt-image-2`, `gemini-3.1-flash-image-preview`, `gemini-3-pro-image-preview` |
| `gemini` | Google image-capable Gemini model identifiers, such as `gemini-3-pro-image-preview` |
| `xai` | xAI image model identifiers, such as `grok-imagine-image` |

For Alibaba Cloud, set `ALIYUN_API_BASE` to a base URL in the same region and
workspace as `DASHSCOPE_API_KEY`. For example:

```bash
# Beijing workspace
ALIYUN_API_BASE=https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api/v1

# Singapore workspace
ALIYUN_API_BASE=https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/api/v1
```

Alibaba Cloud APIs require pixel dimensions rather than a bare aspect ratio.
The adapter maps common ratios to documented recommended dimensions—for
example, `16:9` becomes `1280*720` for Qwen/Z-Image and `2688*1536` for Wan.
Explicit dimensions such as `1024*1536` are passed through unchanged.

For Volcano Engine Ark, configure `ARK_API_KEY`. The `model` request field must
be a Model ID or Endpoint ID enabled in the Ark console. Common aspect ratios
are mapped to compatible 2K pixel sizes—for example, `16:9` becomes
`2848x1600`. Resolution tiers (`1K`, `1.5K`, `2K`, `3K`, or `4K`, subject to the
selected Seedream model) and explicit dimensions are passed through.

### Create image generation task

- **POST** `/v1/images/generations/async`
- Body: same as the synchronous endpoint.
- Response (`202 Accepted`):
  ```json
  {
    "task_id": "uuid-string"
  }
  ```

### Get image generation task

- **GET** `/v1/images/generations/async/<task_id>`
- Processing response:
  ```json
  {
    "status": "processing",
    "provider": "toapis",
    "model": "gemini-3.1-flash-image-preview",
    "created_at": 1700000000.0,
    "task_id": "uuid-string"
  }
  ```
- Success response contains `status: "success"` and `image_base64`; failure
  contains `status: "failed"` and `error`. Results are stored in Redis for 7 days.

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
