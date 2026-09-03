"""Markdown → WeChat article conversion and draft publishing.

Ported and adapted from the wewrite project (toolkit/converter.py,
theme.py, wechat_api.py, publisher.py). Provides:

- WeChatConverter / load_theme / list_themes: Markdown -> inline-style HTML
- get_access_token / upload_image_bytes / upload_thumb_bytes: WeChat media API
- create/list/get/update/delete draft-box articles
"""

from .converter import WeChatConverter, ConvertResult, preview_html, rewrite_image_srcs
from .theme import Theme, load_theme, list_themes
from .wechat_api import (
    get_access_token,
    upload_image_bytes,
    upload_thumb_bytes,
    load_image_bytes,
)
from .publisher import (
    DraftResult,
    WeChatDraftAPIError,
    create_draft,
    delete_draft,
    get_draft,
    list_drafts,
    update_draft,
)

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
    "list_drafts",
    "get_draft",
    "update_draft",
    "delete_draft",
    "DraftResult",
    "WeChatDraftAPIError",
]
