"""Markdown → WeChat article conversion and draft publishing.

Ported and adapted from the wewrite project (toolkit/converter.py,
theme.py, wechat_api.py, publisher.py). Provides:

- WeChatConverter / load_theme / list_themes: Markdown -> inline-style HTML
- get_access_token / upload_image_bytes / upload_thumb_bytes: WeChat media API
- create_draft: push an article into the WeChat draft box
"""

from .converter import WeChatConverter, ConvertResult, preview_html, rewrite_image_srcs
from .theme import Theme, load_theme, list_themes
from .wechat_api import (
    get_access_token,
    upload_image_bytes,
    upload_thumb_bytes,
    load_image_bytes,
)
from .publisher import create_draft, DraftResult

__all__ = [
    "WeChatConverter",
    "ConvertResult",
    "preview_html",
    "rewrite_image_srcs",
    "Theme",
    "load_theme",
    "list_themes",
    "get_access_token",
    "upload_image_bytes",
    "upload_thumb_bytes",
    "load_image_bytes",
    "create_draft",
    "DraftResult",
]
