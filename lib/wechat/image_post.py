"""Native picture-message drafts (newspic), separate from HTML articles."""

import re
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image

from .publisher import DraftResult, WeChatDraftAPIError, _post_draft_api

MAX_IMAGES = 20
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_CONTENT_BYTES = 2 * 1024


def validate_image_post(payload: dict) -> dict:
    """Validate the complete request before token lookup or material uploads."""
    title = payload.get("title")
    content = payload.get("content")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 32:
        raise ValueError("title must contain 1 to 32 characters")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be non-empty plain text")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError("content must not exceed 2048 UTF-8 bytes")
    if re.search(r"</?[A-Za-z][^>]*>", content):
        raise ValueError("content must be plain text, without HTML")
    images = payload.get("images")
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGES:
        raise ValueError("images must contain 1 to 20 remote image URLs")
    for source in images:
        if not isinstance(source, str):
            raise TypeError("each image must be an HTTP(S) URL")
        url = urlsplit(source)
        if url.scheme not in ("http", "https") or not url.hostname or url.username:
            raise ValueError("each image must be an HTTP(S) URL without credentials")
    if len(set(images)) != len(images):
        raise ValueError("duplicate image URLs are not allowed")
    for key in ("need_open_comment", "only_fans_can_comment"):
        value = payload.get(key, 0)
        if type(value) is not int or value not in (0, 1):
            raise ValueError(f"{key} must be 0 or 1")
    return {
        "title": title.strip(),
        "content": content,
        "images": images,
        "need_open_comment": payload.get("need_open_comment", 0),
        "only_fans_can_comment": payload.get("only_fans_can_comment", 0),
    }


def validate_picture(data: bytes) -> None:
    """Reject invalid or oversized files before any permanent uploads."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("each image must be non-empty and at most 10 MiB")
    try:
        with Image.open(BytesIO(data)) as picture:
            if picture.format not in ("JPEG", "PNG"):
                raise ValueError("picture drafts currently accept PNG and JPEG images")
            picture.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid image file") from exc


def create_image_draft(
    access_token: str,
    title: str,
    content: str,
    image_media_ids: list[str],
    need_open_comment: int = 0,
    only_fans_can_comment: int = 0,
) -> DraftResult:
    if not 1 <= len(image_media_ids) <= MAX_IMAGES or any(
        not isinstance(value, str) or not value.strip() for value in image_media_ids
    ):
        raise ValueError("1 to 20 non-empty permanent image media IDs are required")
    article = {
        "article_type": "newspic",
        "title": title,
        "content": content,
        "need_open_comment": need_open_comment,
        "only_fans_can_comment": only_fans_can_comment,
        "image_info": {
            "image_list": [{"image_media_id": value} for value in image_media_ids]
        },
    }
    result = _post_draft_api("add", access_token, {"articles": [article]})
    media_id = result.get("media_id")
    if not isinstance(media_id, str) or not media_id.strip():
        raise WeChatDraftAPIError("add", "invalid_response", "missing media_id")
    return DraftResult(media_id=media_id)
