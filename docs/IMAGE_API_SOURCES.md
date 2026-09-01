# 图片生成 API 来源文档

> 最后核对日期：2026-09-01

本文集中记录统一图片生成 API 所接入服务的来源文档、协议差异和项目实现位置，便于故障排查、二次接入及后续升级。

## 统一接口与实现

本项目对外提供：

| 接口 | 功能 |
| :--- | :--- |
| `GET /v1/images/models` | 返回项目保存的 provider/model 目录快照 |
| `POST /v1/images/generations` | 同步生成图片；异步上游由服务端轮询并转换为同步响应 |
| `POST /v1/images/generations/async` | 创建本地异步生成任务 |
| `GET /v1/images/generations/async/<task_id>` | 查询本地异步任务 |

核心实现：

- `lib/image_generation.py`：provider registry、模型目录、参数转换、上游调用和任务轮询。
- `lib/object_storage.py`：Cloudflare R2/S3 上传、对象路径和 CDN URL。
- `server.py`：统一 HTTP 路由、Redis 任务状态和响应格式。
- `tests/test_image_generation.py`：provider 协议单元测试。
- `tests/test_image_api.py`：统一 HTTP API 测试。
- `tests/test_object_storage.py`：S3/R2 存储测试。

统一请求公开 `provider`、`model`、`prompt`、`size`、`images` 和 `return_url`。`images` 为可选数组，支持 1–3 个公网 URL、Base64 data URI 或裸 Base64，用于图像编辑、多图融合和参考生成。Base64 输入仅接受跨 provider 可移植的 JPEG、PNG、WebP，并执行完整图片校验。Qwen Image、Wan 2.6、ToAPIs 的单图上限为 10 MB，其余适配器为 20 MB；ToAPIs 只接受 URL。不同上游的 `size` 和输入图片字段由适配器转换。生成结果默认返回 `image_base64`；`return_url=true` 时上传 R2 并返回 `image_url`。

图生图沿用现有两个生成入口，不增加独立路由：

```json
{
  "provider": "ark",
  "model": "doubao-seedream-4-5-251128",
  "prompt": "保留主体和构图，转换成水彩插画风格",
  "images": ["https://cdn.example/reference.jpg"],
  "size": "1:1",
  "return_url": true
}
```

Provider 映射如下：

| Provider | 上游图生图协议 | 统一接口限制 |
| :--- | :--- | :--- |
| `gemini` | `contents[].parts[].inlineData` | URL 由服务端安全下载后转 Base64；拒绝内网地址和非 80/443 端口 |
| `xai` | `POST /images/edits`，单图用 `image`，多图用 `images` | URL、data URI、裸 Base64 |
| `ark` | `POST /images/generations` 的 `image` 数组 | URL、data URI、裸 Base64 |
| `aliyun` | `messages[].content[].image`，图片位于文本指令前 | Qwen、Wan 2.7/2.6 支持；Z-Image 和 `wan2.5-t2i-preview` 不支持 |
| `apimart` | `image_urls` | URL、data URI、裸 Base64 |
| `toapis` | `image_urls`（GPT-Image 会在上游归一化为 `reference_images`） | 上游当前只接受公网 URL |

## Provider 总览

| Provider | 上游协议 | 项目凭证变量 | 默认 API Base |
| :--- | :--- | :--- | :--- |
| `gemini` | Google `generateContent`，同步 | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com` |
| `xai` | OpenAI Images 风格，同步 | `X_AI_API_KEY` | `https://api.x.ai/v1` |
| `ark` | 火山方舟 Images API，同步 | `ARK_API_KEY` | `https://ark.cn-beijing.volces.com/api/v3` |
| `aliyun` | 百炼同步多模态或异步任务 | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/api/v1` |
| `apimart` | 提交任务 + 查询任务 | `APIMART_API_KEY` | `https://api.apimart.ai/v1` |
| `toapis` | 提交任务 + 查询图片任务 | `TOAPIS_API_KEY` | `https://toapis.com/v1`；中国大陆可使用 `https://toapis.xyz/v1` |

密钥只应配置在 `.env`、部署平台 Secret 或密钥管理服务中，禁止写入本文或提交到 Git。

## Google Gemini

### 来源

- 官方图片生成指南：<https://ai.google.dev/gemini-api/docs/image-generation>
- 官方 Gemini API 文档：<https://ai.google.dev/gemini-api/docs>
- 本地参考实现：`~/Workspace/bin/image-creator`

### 对接信息

- Endpoint：`POST {GEMINI_API_BASE}/v1/models/{model}:generateContent`
- 鉴权：`x-goog-api-key` Header，项目中来自 `GEMINI_API_KEY`。
- 响应图片：`candidates[0].content.parts[].inlineData.data`。
- 图生图：统一 `images` 会转换成 `parts[].inlineData`；URL 由服务端下载并转换，Base64 直接解码后重新编码。
- 为防止服务端请求伪造，Gemini URL 输入只允许解析到公网地址的 HTTP(S) URL、80/443 端口，并逐次校验重定向目标；实际连接固定使用已验证 IP，同时保留原域名的 TLS SNI 与证书校验，避免 DNS 重绑定。
- `size` 映射到当前 REST 协议的 `generationConfig.responseFormat.image`：分辨率使用 `imageSize`，宽高比使用 `aspectRatio`。
- 当前首选模型为 `gemini-3.1-flash-image`、`gemini-3-pro-image`；目录暂时保留两个 `*-preview` ID 作为兼容别名。实际可用模型取决于 API Key、地域和 Google 的模型生命周期。

## xAI

### 来源

- 官方图片生成指南：<https://docs.x.ai/docs/guides/image-generation>
- 官方 API 文档：<https://docs.x.ai/docs/api-reference>
- 本地参考实现：`~/Workspace/bin/image-creator`

### 对接信息

- Endpoint：`POST {X_AI_API_BASE}/images/generations`
- 鉴权：`Authorization: Bearer {X_AI_API_KEY}`。
- 响应兼容 `data[0].b64_json` 和 `data[0].url`。
- 文生图使用 `/images/generations`；存在 `images` 时切换到 JSON 协议的 `/images/edits`。
- 单张输入映射为 `image={type,url}`，多张输入映射为 `images=[...]`；请求继续指定 `response_format=b64_json`，避免生产环境二次访问临时 CDN。
- 当前首选模型：`grok-imagine-image-2.0`；保留 `grok-imagine-image` 兼容别名。
- 分辨率映射为 `resolution`，宽高比映射为 `aspect_ratio`。

## 火山引擎方舟 Ark

### 来源

- 图片生成 Endpoint：`POST https://ark.cn-beijing.volces.com/api/v3/images/generations`
- Base URL 与鉴权：<https://docs.volcengine.com/docs/82379/1298459>
- 模型 ID / Endpoint ID：<https://docs.volcengine.com/docs/82379/1330310>
- Seedream 4.0–5.0 提示词指南：<https://docs.volcengine.com/docs/82379/1829186>
- 图片 API 错误码：<https://docs.volcengine.com/docs/82379/1299023>

### 对接信息

- 鉴权：`Authorization: Bearer {ARK_API_KEY}`。
- 请求固定使用 `response_format=b64_json`、`watermark=false`，避免再次下载临时 URL。
- 图生图将统一 `images` 原样映射为上游 `image` 数组；Ark 支持 URL 和 Base64 data URI。
- 如果上游返回 `data[0].url`，项目仍会下载并转换。
- `model` 可使用已开通的 Model ID 或 Ark Endpoint ID。
- 对接范围：Doubao Seedream 5.0 Pro、Seedream 5.0 Lite、Seedream 4.5、Seedream 4.0。
- 已真实验证：`doubao-seedream-4-0-250828`。
- 常见宽高比会转换为兼容的 2K 像素尺寸；显式分辨率档位和 `宽x高` 会透传。

## 阿里云百炼 Aliyun

### 来源

- 文生图概览：<https://help.aliyun.com/zh/model-studio/text-to-image>
- 千问图像生成与编辑 3.0：<https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference>
- 千问 Qwen-Image：<https://help.aliyun.com/zh/model-studio/qwen-image-api>
- 万相图像生成与编辑 2.7：<https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference>
- 万相图像生成与编辑 2.6：<https://help.aliyun.com/zh/model-studio/wan-image-generation-api-reference>
- 万相文生图 V2：<https://help.aliyun.com/zh/model-studio/text-to-image-v2-api-reference>
- Z-Image：<https://help.aliyun.com/zh/model-studio/z-image-api-reference>
- API Key 配置：<https://help.aliyun.com/zh/model-studio/get-api-key>
- 错误码：<https://help.aliyun.com/zh/model-studio/error-code>

### 对接信息

同步模型族：

- `qwen-image*`
- `z-image*`

同步 Endpoint：

```text
POST {ALIYUN_API_BASE}/services/aigc/multimodal-generation/generation
```

异步模型族：

- `wan*`

异步流程：

```text
POST {ALIYUN_API_BASE}/services/aigc/image-generation/generation
X-DashScope-Async: enable

GET {ALIYUN_API_BASE}/tasks/{task_id}
```

补充说明：

- 鉴权：`Authorization: Bearer {DASHSCOPE_API_KEY}`。
- 任务成功状态为 `SUCCEEDED`；失败终态包括 `FAILED`、`CANCELED`、`UNKNOWN`。
- 图片 URL 位于 `output.choices[0].message.content[].image`，有效期通常为 24 小时，项目会立即下载。
- Qwen Image 和 Wan 图生图会把每个输入转换为 `messages[0].content` 中的 `{"image": "..."}`，再追加唯一的 `{"text": "..."}` 指令。
- 统一接口最多输入 3 张图；这符合 Qwen Image 的上限。Wan 上游允许更多输入，但统一接口当前保持跨 provider 一致。
- `z-image-turbo` 和 `wan2.5-t2i-preview` 仅支持文生图，传入 `images` 时适配器会在计费调用前拒绝。
- API Key、模型和 `ALIYUN_API_BASE` 必须属于同一地域及 Workspace。
- Workspace 专属域名优先于公共 DashScope 域名。
- 已真实验证：`qwen-image-3.0-pro`。

## APIMart

### 来源

- 官网：<https://apimart.ai>
- 文档首页：<https://docs.apimart.ai>
- GPT-Image-2：<https://docs.apimart.ai/cn/api-reference/images/gpt-image-2/generation.md>
- Nano Banana 2 / Gemini 3.1 Flash：<https://docs.apimart.ai/cn/api-reference/images/gemini-3.1-flash/generation.md>
- Nano Banana Pro / Gemini 3 Pro：<https://docs.apimart.ai/cn/api-reference/images/gemini-3-pro/generation.md>
- 任务状态：<https://docs.apimart.ai/cn/api-reference/tasks/status.md>
- 文档索引：<https://docs.apimart.ai/llms.txt>

### 对接信息

```text
POST {APIMART_API_BASE}/images/generations
GET  {APIMART_API_BASE}/tasks/{task_id}
```

- 鉴权：`Authorization: Bearer {APIMART_API_KEY}`。
- 创建响应任务 ID：`data[0].task_id`。
- 成功状态：`data.status=completed`。
- 图片 URL：`data.result.images[0].url[0]`。
- 已真实验证：`gpt-image-2`。
- 图生图使用 `image_urls`，支持公网 URL 和 Base64 data URI 混合输入。
- 兼容文档中的 GPT-Image 和 Nano Banana 模型别名；目录详见 `GET /v1/images/models`。

## ToAPIs

### 来源

- 官网：<https://toapis.com>
- 快速开始：<https://docs.toapis.com/docs/cn/quickstart.md>
- GPT-Image-2：<https://docs.toapis.com/docs/cn/api-reference/images/gpt-image-2/generation.md>
- Nano Banana 2 / Gemini 3.1 Flash：<https://docs.toapis.com/docs/cn/api-reference/images/gemini-3.1-flash/generation.md>
- Nano Banana Pro / Gemini 3 Pro：<https://docs.toapis.com/docs/cn/api-reference/images/gemini-3-pro-image/generation.md>
- 图片任务状态：<https://docs.toapis.com/docs/cn/api-reference/tasks/image-status.md>
- 文档索引：<https://docs.toapis.com/llms.txt>

### 对接信息

```text
POST {TOAPIS_API_BASE}/images/generations
GET  {TOAPIS_API_BASE}/images/generations/{task_id}
```

- 鉴权：`Authorization: Bearer {TOAPIS_API_KEY}`。
- 创建响应任务 ID：顶层 `id`。
- 任务状态：`queued`、`in_progress`、`completed`、`failed`。
- 图片 URL：`result.data[0].url`，通常有效 24 小时。
- 图生图统一发送 `image_urls`，兼容 Gemini/Nano Banana；GPT-Image 会由 ToAPIs 归一化为 `reference_images`。ToAPIs 已停止在这些字段中接收 Base64，因此统一接口会提前拒绝非 HTTP(S) 输入。
- 未指定 `size` 时不发送该字段，由模型采用自身默认值；不会补充不兼容的 `size=auto`。
- 已真实验证：`gpt-image-2`，API Base 使用 `https://toapis.xyz/v1`。

## Cloudflare R2 / S3

### 来源

- R2 S3 API：<https://developers.cloudflare.com/r2/api/s3/api/>
- R2 API Tokens：<https://developers.cloudflare.com/r2/api/tokens/>
- boto3 S3 客户端：<https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html>

### 对接信息

- 配置变量：`R2_ENDPOINT`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_CDN_URL`、`R2_REGION`、`R2_KEY_PREFIX`。
- 上传使用 S3 `PutObject`，不设置 ACL；公开访问由 R2 自定义域名/CDN 配置负责。
- 对象键：`<R2_KEY_PREFIX>/YYYY/MM/DD/<uuid>.<ext>`，日期使用 UTC。
- 图片扩展名和 MIME 类型从实际图片内容检测，不信任上游 URL 后缀。
- 已真实验证 R2 上传和通过 `R2_CDN_URL` 下载。

## 更新流程

升级任一 provider 时应同步完成：

1. 核对本文中的官方文档和上游 Endpoint。
2. 更新 `lib/image_generation.py` 中的请求/响应适配器。
3. 更新 `IMAGE_MODEL_CATALOG` 和 `IMAGE_MODEL_CATALOG_UPDATED_AT`。
4. 更新对应环境变量说明及 `docker-compose.yml`。
5. 为协议变化增加单元测试。
6. 使用低成本提示词进行一次真实 smoke test，记录测试模型，但不要记录 API Key。
7. 更新本文“最后核对日期”和相关 provider 说明。

## 已知运维注意事项

- 图片生成 POST 不做自动重试，避免重复计费；任务查询 GET 可重试。
- HTTP 请求体默认上限为 85 MiB，可通过 `MAX_REQUEST_BYTES` 调整；图片仍受 provider 单图限制。
- APIMart、ToAPIs、Aliyun Wan 的同步外观由服务端轮询实现。
- 上游临时 URL 必须立即下载；`return_url=true` 可持久化到 R2。
- `IMAGE_GENERATION_MAX_WAIT` 和 `IMAGE_GENERATION_POLL_INTERVAL` 控制轮询。
- Provider 模型可能下线、改名或受地域限制；排查时先调用 `GET /v1/images/models`，再对照对应官方文档和控制台实际授权。
