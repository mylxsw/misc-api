"""WeChat draft-box publishing.

Adapted from wewrite (toolkit/publisher.py). Creates an article draft via
the WeChat Official Account draft/add API.
"""

import json
from dataclasses import dataclass
from typing import Optional

import requests

API_TIMEOUT = 30


@dataclass
class DraftResult:
    media_id: str


def create_draft(
    access_token: str,
    title: str,
    html: str,
    digest: str,
    thumb_media_id: Optional[str] = None,
    author: Optional[str] = None,
    content_source_url: Optional[str] = None,
    show_cover_pic: int = 0,
) -> DraftResult:
    """
    Create a draft in the WeChat Official Account draft box.
    API: POST https://api.weixin.qq.com/cgi-bin/draft/add
    Returns DraftResult. Raise ValueError on error (errcode present and != 0).
    """
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

    body = {"articles": [article]}

    # MUST use ensure_ascii=False — otherwise Chinese becomes \uXXXX and
    # WeChat stores the escape sequences literally, causing title length
    # overflow and garbled content.
    resp = requests.post(
        "https://api.weixin.qq.com/cgi-bin/draft/add",
        params={"access_token": access_token},
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=API_TIMEOUT,
    )

    data = resp.json()

    errcode = data.get("errcode", 0)
    if errcode != 0:
        errmsg = data.get("errmsg", "unknown error")
        raise ValueError(f"WeChat create_draft error: errcode={errcode}, errmsg={errmsg}")

    if "media_id" not in data:
        raise ValueError(f"WeChat create_draft error: missing media_id in response: {data}")

    return DraftResult(media_id=data["media_id"])
