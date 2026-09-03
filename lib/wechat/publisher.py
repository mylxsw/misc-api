"""WeChat Official Account draft-box API helpers."""

import json
from dataclasses import dataclass
from typing import Any

import requests

API_TIMEOUT = 30
DRAFT_API_BASE = "https://api.weixin.qq.com/cgi-bin/draft"


class WeChatDraftAPIError(ValueError):
    """A structured error returned by or while calling the WeChat draft API."""

    def __init__(self, operation: str, errcode: int | str, errmsg: str):
        self.operation = operation
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(
            f"WeChat {operation} error: errcode={errcode}, errmsg={errmsg}"
        )


@dataclass
class DraftResult:
    media_id: str


def _post_draft_api(
    operation: str,
    access_token: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST JSON to a WeChat draft endpoint and normalize transport/API errors."""
    try:
        response = requests.post(
            f"{DRAFT_API_BASE}/{operation}",
            params={"access_token": access_token},
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=API_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise WeChatDraftAPIError(operation, "transport_error", str(exc)) from exc
    except ValueError as exc:
        raise WeChatDraftAPIError(
            operation, "invalid_response", "WeChat returned invalid JSON"
        ) from exc

    if not isinstance(data, dict):
        raise WeChatDraftAPIError(
            operation, "invalid_response", "WeChat returned a non-object response"
        )

    errcode = data.get("errcode", 0)
    if errcode != 0:
        raise WeChatDraftAPIError(
            operation, errcode, data.get("errmsg", "unknown error")
        )
    return data


def create_draft(
    access_token: str,
    title: str,
    html: str,
    digest: str,
    thumb_media_id: str | None = None,
    author: str | None = None,
    content_source_url: str | None = None,
    show_cover_pic: int = 0,
) -> DraftResult:
    """Create a draft and return its media ID."""
    article = {
        "title": title,
        "author": author or "",
        "digest": digest,
        "content": html,
        "show_cover_pic": show_cover_pic,
    }
    if thumb_media_id:
        article["thumb_media_id"] = thumb_media_id
    if content_source_url:
        article["content_source_url"] = content_source_url

    data = _post_draft_api("add", access_token, {"articles": [article]})
    if "media_id" not in data:
        raise WeChatDraftAPIError(
            "add", "invalid_response", "missing media_id in response"
        )
    return DraftResult(media_id=data["media_id"])


def list_drafts(
    access_token: str,
    offset: int = 0,
    count: int = 10,
    no_content: int = 0,
) -> dict[str, Any]:
    """Return a page of drafts from the WeChat draft box."""
    return _post_draft_api(
        "batchget",
        access_token,
        {"offset": offset, "count": count, "no_content": no_content},
    )


def get_draft(access_token: str, media_id: str) -> dict[str, Any]:
    """Return one draft by media ID."""
    return _post_draft_api("get", access_token, {"media_id": media_id})


def update_draft(
    access_token: str,
    media_id: str,
    index: int,
    article: dict[str, Any],
) -> dict[str, Any]:
    """Replace one article in a multi-article draft."""
    return _post_draft_api(
        "update",
        access_token,
        {"media_id": media_id, "index": index, "articles": article},
    )


def delete_draft(access_token: str, media_id: str) -> dict[str, Any]:
    """Permanently delete a draft by media ID."""
    return _post_draft_api("delete", access_token, {"media_id": media_id})
