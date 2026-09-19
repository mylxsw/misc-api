"""MCP tool surface for the misc-api HTTP endpoints."""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal, TypedDict

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from lib.mcp.dispatch import invoke_route, omit_none, wechat_headers

INSTRUCTIONS = """\
AI media service exposing image generation, TTS, and WeChat Official Account tools.

Image generation:
- Call list_image_models if the caller has not named a provider and model.
- generate_image waits for the provider. Prefer create_image_generation_task plus
  get_image_generation_task for slow providers (APIMart, ToAPIs, Alibaba Wan).
- Set return_url=true when object storage is configured to avoid huge Base64 payloads.

Voice:
- CosyVoice is synchronous by default; use the async CosyVoice tools for long text.
- Podcast and Fish Audio always run as background tasks. Poll the matching get_* tool.

WeChat:
- Credentials default to WECHAT_APPID / WECHAT_SECRET on the server.
- Only pass appid and secret when the caller supplies a specific Official Account.
- delete_wechat_draft is permanent and cannot be undone. Confirm media_id first.
"""

mcp = MCPServer(
    name="misc-api",
    title="AI Media Services",
    version="0.1.0",
    instructions=INSTRUCTIONS,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
READ_ONLY_OPEN = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, open_world_hint=True
)
MUTATING_OPEN = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, open_world_hint=True
)
DESTRUCTIVE_OPEN = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, open_world_hint=True
)


def mcp_http_max_body_size() -> int:
    """Reuse the Flask request-body limit for Streamable HTTP."""
    return int(os.getenv("MAX_REQUEST_BYTES", str(85 * 1024 * 1024)))


class PodcastScript(TypedDict):
    speaker: str
    text: str


# --- Images -----------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def list_image_models() -> dict[str, Any]:
    """List supported image providers and their model identifiers."""
    return invoke_route("GET", "/v1/images/models")


@mcp.tool(annotations=MUTATING_OPEN)
def generate_image(
    provider: Annotated[
        str,
        Field(description="aliyun, ark, apimart, toapis, gemini, or xai"),
    ],
    model: Annotated[str, Field(description="Provider-specific model identifier")],
    prompt: Annotated[str, Field(description="Image description")],
    size: Annotated[
        str | None,
        Field(
            description="Legacy size: a resolution tier (1K, 2K, 4K) or aspect ratio"
        ),
    ] = None,
    aspect_ratio: Annotated[
        str | None,
        Field(description="Output aspect ratio such as 1:1, 16:9, or 21:9"),
    ] = None,
    resolution: Annotated[
        str | None,
        Field(description="Output resolution tier such as 1K, 2K, or 4K"),
    ] = None,
    images: Annotated[
        list[str] | None,
        Field(
            description=(
                "One to three reference images as public URLs, data URIs, or Base64"
            )
        ),
    ] = None,
    return_url: Annotated[
        bool,
        Field(
            description="Upload to configured S3/R2 storage and return image_url"
        ),
    ] = False,
    record_history: Annotated[
        bool,
        Field(description="Save the completed generation to the history API"),
    ] = False,
) -> dict[str, Any]:
    """Generate one image and wait for the provider. Returns image_base64 or image_url."""
    return invoke_route(
        "POST",
        "/v1/images/generations",
        json_body=omit_none(
            {
                "provider": provider,
                "model": model,
                "prompt": prompt,
                "size": size,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "images": images,
                "return_url": return_url,
                "record_history": record_history,
            }
        ),
    )


@mcp.tool(annotations=MUTATING_OPEN)
def create_image_generation_task(
    provider: Annotated[
        str,
        Field(description="aliyun, ark, apimart, toapis, gemini, or xai"),
    ],
    model: str,
    prompt: str,
    size: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    images: list[str] | None = None,
    return_url: bool = False,
    record_history: bool = False,
) -> dict[str, Any]:
    """Create a background image-generation task. Returns task_id; poll with get_image_generation_task."""
    return invoke_route(
        "POST",
        "/v1/images/generations/async",
        json_body=omit_none(
            {
                "provider": provider,
                "model": model,
                "prompt": prompt,
                "size": size,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "images": images,
                "return_url": return_url,
                "record_history": record_history,
            }
        ),
    )


@mcp.tool(annotations=READ_ONLY)
def get_image_generation_task(task_id: str) -> dict[str, Any]:
    """Get an image-generation task. status is processing, success, or failed."""
    return invoke_route("GET", f"/v1/images/generations/async/{task_id}")


@mcp.tool(annotations=READ_ONLY)
def stitch_images(
    images: Annotated[
        list[str],
        Field(description="Image URLs or Base64 strings to stitch together"),
    ],
    direction: Annotated[
        Literal["horizontal", "vertical"],
        Field(description="Stitch direction"),
    ] = "horizontal",
) -> dict[str, Any]:
    """Stitch multiple images horizontally or vertically. Returns image_b64."""
    return invoke_route(
        "POST",
        "/v1/image/stitch",
        json_body={"images": images, "direction": direction},
    )


# --- Voice ------------------------------------------------------------------


@mcp.tool(annotations=MUTATING_OPEN)
def cosyvoice_tts(
    text: Annotated[str, Field(description="Text to synthesize")],
    voice: Annotated[str, Field(description="CosyVoice voice id")] = "libai_v2",
    model: Annotated[str, Field(description="CosyVoice model id")] = "cosyvoice-v2",
    volume: float | None = None,
    speech_rate: float | None = None,
    pitch_rate: float | None = None,
    instruction: str | None = None,
    language_hints: list[str] | None = None,
) -> dict[str, Any]:
    """Synthesize speech with Alibaba Cloud DashScope CosyVoice. Returns voice_b64."""
    return invoke_route(
        "POST",
        "/v1/voice/cosyvoice",
        json_body=omit_none(
            {
                "text": text,
                "voice": voice,
                "model": model,
                "volume": volume,
                "speech_rate": speech_rate,
                "pitch_rate": pitch_rate,
                "instruction": instruction,
                "language_hints": language_hints,
            }
        ),
    )


@mcp.tool(annotations=MUTATING_OPEN)
def create_cosyvoice_task(
    text: str,
    voice: str = "libai_v2",
    model: str = "cosyvoice-v2",
    volume: float | None = None,
    speech_rate: float | None = None,
    pitch_rate: float | None = None,
    instruction: str | None = None,
    language_hints: list[str] | None = None,
) -> dict[str, Any]:
    """Create a background CosyVoice TTS task. Returns task_id; poll with get_cosyvoice_task."""
    return invoke_route(
        "POST",
        "/v1/voice/cosyvoice/async",
        json_body=omit_none(
            {
                "text": text,
                "voice": voice,
                "model": model,
                "volume": volume,
                "speech_rate": speech_rate,
                "pitch_rate": pitch_rate,
                "instruction": instruction,
                "language_hints": language_hints,
            }
        ),
    )


@mcp.tool(annotations=READ_ONLY)
def get_cosyvoice_task(task_id: str) -> dict[str, Any]:
    """Get a CosyVoice TTS task. status is processing, success, or failed."""
    return invoke_route("GET", f"/v1/voice/cosyvoice/async/{task_id}")


@mcp.tool(annotations=MUTATING_OPEN)
def create_podcast_task(
    scripts: Annotated[
        list[PodcastScript],
        Field(description="Ordered speaker/text turns. Use matching speaker series."),
    ],
    use_head_music: bool = False,
    use_tail_music: bool = False,
) -> dict[str, Any]:
    """Create a Volcano Engine multi-speaker podcast task. Returns task_id."""
    return invoke_route(
        "POST",
        "/v1/voice/podcast",
        json_body={
            "scripts": scripts,
            "use_head_music": use_head_music,
            "use_tail_music": use_tail_music,
        },
    )


@mcp.tool(annotations=READ_ONLY)
def get_podcast_task(task_id: str) -> dict[str, Any]:
    """Get a podcast-generation task. Success includes voice_b64."""
    return invoke_route("GET", f"/v1/voice/podcast/{task_id}")


@mcp.tool(annotations=MUTATING_OPEN)
def create_fish_audio_task(
    text: Annotated[str, Field(description="Text to convert to speech")],
    reference_id: Annotated[
        str | None, Field(description="Platform voice reference id")
    ] = None,
    reference_audio: Annotated[
        str | None,
        Field(description="Server-side path to a local reference audio file"),
    ] = None,
    speed: float = 1.0,
    volume: int = 0,
    format: Literal["mp3", "wav", "pcm", "opus"] = "mp3",
    latency: Literal["normal", "balanced"] = "normal",
) -> dict[str, Any]:
    """Create a Fish Audio TTS task. Returns task_id; poll with get_fish_audio_task."""
    return invoke_route(
        "POST",
        "/v1/voice/fish-audio/text-to-speech",
        json_body=omit_none(
            {
                "text": text,
                "reference_id": reference_id,
                "reference_audio": reference_audio,
                "speed": speed,
                "volume": volume,
                "format": format,
                "latency": latency,
            }
        ),
    )


@mcp.tool(annotations=READ_ONLY)
def get_fish_audio_task(task_id: str) -> dict[str, Any]:
    """Get a Fish Audio TTS task. Success includes voice_b64."""
    return invoke_route("GET", f"/v1/voice/fish-audio/text-to-speech/{task_id}")


# --- WeChat -----------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def list_wechat_themes() -> dict[str, Any]:
    """List Markdown layout themes available for WeChat article preview and drafts."""
    return invoke_route("GET", "/v1/wechat/markdown/themes")


@mcp.tool(annotations=READ_ONLY)
def preview_wechat_article(
    markdown: Annotated[str, Field(description="Markdown source")],
    theme: str = "professional-clean",
    title: str | None = None,
    full_page: Annotated[
        bool,
        Field(description="Wrap body HTML in a full HTML document for browser preview"),
    ] = False,
) -> dict[str, Any]:
    """Convert Markdown to WeChat-compatible HTML. Does not require WeChat credentials."""
    return invoke_route(
        "POST",
        "/v1/wechat/markdown/preview",
        json_body=omit_none(
            {
                "markdown": markdown,
                "theme": theme,
                "title": title,
                "full_page": full_page,
            }
        ),
    )


@mcp.tool(annotations=MUTATING_OPEN)
def publish_wechat_draft(
    markdown: Annotated[str, Field(description="Markdown source")],
    theme: str = "professional-clean",
    title: str | None = None,
    author: str | None = None,
    digest: Annotated[
        str | None, Field(description="Override the summary (max 120 UTF-8 bytes)")
    ] = None,
    cover: Annotated[
        str | None,
        Field(description="Cover image as URL, data URI, or Base64"),
    ] = None,
    content_source_url: Annotated[
        str | None, Field(description="阅读原文 / original-article link")
    ] = None,
    appid: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Convert Markdown, upload images, and create a WeChat Official Account news draft."""
    return invoke_route(
        "POST",
        "/v1/wechat/markdown/draft",
        json_body=omit_none(
            {
                "markdown": markdown,
                "theme": theme,
                "title": title,
                "author": author,
                "digest": digest,
                "cover": cover,
                "content_source_url": content_source_url,
                "appid": appid,
                "secret": secret,
            }
        ),
        headers=wechat_headers(appid, secret),
    )


@mcp.tool(annotations=MUTATING_OPEN)
def create_wechat_image_draft(
    title: Annotated[str, Field(description="Picture-post title, 1 to 32 characters")],
    content: Annotated[
        str, Field(description="Plain-text body without HTML, max 2048 UTF-8 bytes")
    ],
    images: Annotated[
        list[str],
        Field(description="1 to 20 unique HTTP(S) PNG/JPEG URLs; first image is the cover"),
    ],
    need_open_comment: Literal[0, 1] = 0,
    only_fans_can_comment: Literal[0, 1] = 0,
    appid: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Create a native WeChat picture draft (article_type newspic). Does not publish or mass-send."""
    return invoke_route(
        "POST",
        "/v1/wechat/images/draft",
        json_body=omit_none(
            {
                "title": title,
                "content": content,
                "images": images,
                "need_open_comment": need_open_comment,
                "only_fans_can_comment": only_fans_can_comment,
                "appid": appid,
                "secret": secret,
            }
        ),
        headers=wechat_headers(appid, secret),
    )


@mcp.tool(annotations=READ_ONLY_OPEN)
def list_wechat_drafts(
    offset: Annotated[int, Field(description="Pagination offset, >= 0")] = 0,
    count: Annotated[int, Field(description="Page size, 1 to 20")] = 10,
    no_content: Annotated[
        Literal[0, 1],
        Field(description="1 omits article HTML from the list response"),
    ] = 0,
    appid: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """List WeChat Official Account drafts."""
    return invoke_route(
        "GET",
        "/v1/wechat/drafts",
        query={"offset": offset, "count": count, "no_content": no_content},
        headers=wechat_headers(appid, secret),
    )


@mcp.tool(annotations=READ_ONLY_OPEN)
def get_wechat_draft(
    media_id: str,
    appid: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Get a WeChat draft by media_id, including news_item HTML."""
    return invoke_route(
        "GET",
        f"/v1/wechat/drafts/{media_id}",
        headers=wechat_headers(appid, secret),
    )


@mcp.tool(annotations=MUTATING_OPEN)
def update_wechat_draft(
    media_id: str,
    article: Annotated[
        dict[str, Any],
        Field(
            description=(
                "Full replacement article object. title and content are required. "
                "Common fields: author, digest, content_source_url, thumb_media_id, "
                "article_type (news or newspic), need_open_comment, "
                "only_fans_can_comment, image_info, cover_info, product_info. "
                "Fetch the current draft first and keep fields you are not changing."
            )
        ),
    ],
    index: Annotated[int, Field(description="Article index to replace, starting at 0")] = 0,
    cover: Annotated[
        str | None,
        Field(
            description=(
                "Cover image URL, data URI, or Base64. Used when thumb_media_id is empty."
            )
        ),
    ] = None,
    appid: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Replace one article in a WeChat draft. This is a full article replacement."""
    return invoke_route(
        "PUT",
        f"/v1/wechat/drafts/{media_id}",
        json_body=omit_none(
            {
                "index": index,
                "article": article,
                "cover": cover,
                "appid": appid,
                "secret": secret,
            }
        ),
        headers=wechat_headers(appid, secret),
    )


@mcp.tool(annotations=DESTRUCTIVE_OPEN)
def delete_wechat_draft(
    media_id: Annotated[
        str, Field(description="Draft media_id to permanently delete")
    ],
    appid: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Permanently delete a WeChat draft. This cannot be undone."""
    return invoke_route(
        "DELETE",
        f"/v1/wechat/drafts/{media_id}",
        headers=wechat_headers(appid, secret),
    )
