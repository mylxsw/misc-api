"""WeChat Official Account media/token API helpers.

Adapted from wewrite (toolkit/wechat_api.py) for a stateless HTTP service:
images are handled as in-memory bytes (sourced from URLs or base64) rather
than local file paths.
"""

import base64
import binascii
import mimetypes
import time
from dataclasses import dataclass

import requests

# Token cache keyed by appid
_token_cache: dict = {}

# Unified timeout for WeChat API calls
API_TIMEOUT = 30


@dataclass
class TokenResult:
    access_token: str
    expires_at: float  # unix timestamp


def get_access_token(appid: str, secret: str, force_refresh: bool = False) -> str:
    """
    Get access_token with caching and auto-refresh.
    Cache key: appid. Cache until expires_in - 300 seconds (5 min buffer).
    API: GET https://api.weixin.qq.com/cgi-bin/token
    Raise ValueError on API error.
    """
    now = time.time()

    if not force_refresh and appid in _token_cache:
        cached: TokenResult = _token_cache[appid]
        if now < cached.expires_at:
            return cached.access_token

    resp = requests.get(
        "https://api.weixin.qq.com/cgi-bin/token",
        params={
            "grant_type": "client_credential",
            "appid": appid,
            "secret": secret,
        },
        timeout=API_TIMEOUT,
    )
    data = resp.json()

    if "access_token" not in data:
        errcode = data.get("errcode", "unknown")
        errmsg = data.get("errmsg", "unknown error")
        raise ValueError(f"WeChat API error: errcode={errcode}, errmsg={errmsg}")

    access_token = data["access_token"]
    expires_in = data.get("expires_in", 7200)

    _token_cache[appid] = TokenResult(
        access_token=access_token,
        expires_at=now + expires_in - 300,
    )

    return access_token


def _sniff_image_ext(data: bytes) -> str | None:
    """Return a file extension based on the image's magic bytes.

    Covers the formats WeChat's image APIs accept plus common web formats.
    Returns None when the signature is not a recognized image, so callers can
    reject non-image content (an HTML error page, a base64-shaped slug, etc.).
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def load_image_bytes(source: str) -> tuple[bytes, str]:
    """Resolve an image reference to raw bytes + a filename.

    Accepts:
      - http(s) URLs (downloaded)
      - data: URIs with base64 payload (``data:<mime>;base64,<data>``)
      - bare base64 strings

    Returns (data, filename). The extension is sniffed from the decoded image's
    magic bytes so WeChat receives a correctly-typed upload regardless of the
    source's declared type.

    Raises ValueError when the source cannot be resolved to a recognized image
    (a non-base64 data: URI, an invalid base64 string, or content whose bytes
    are not a known image format — e.g. a relative path or an HTML error page),
    so callers can distinguish "not an uploadable image" from a real image.
    """
    if source.startswith(("http://", "https://")):
        resp = requests.get(source, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.content
    else:
        # data: URI — only base64-encoded payloads are supported.
        if source.startswith("data:"):
            header, _, payload = source.partition(",")
            if "base64" not in header:
                raise ValueError("unsupported data: URI (only base64 payloads are accepted)")
            source = payload
        elif "," in source and source.split(",", 1)[0].endswith("base64"):
            source = source.split(",", 1)[1]

        # validate=True makes a non-base64 string raise instead of silently
        # decoding whitespace/garbage into bogus bytes.
        try:
            data = base64.b64decode(source, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"not a valid base64 image: {exc}") from exc

    ext = _sniff_image_ext(data)
    if ext is None:
        raise ValueError("content is not a recognized image format")
    return data, f"image{ext}"


def _content_type_for(filename: str) -> str:
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


def upload_image_bytes(access_token: str, data: bytes, filename: str = "image.jpg") -> str:
    """
    Upload an image for use *inside* article content.
    API: POST https://api.weixin.qq.com/cgi-bin/media/uploadimg
    Returns the hosted url string. Raise ValueError on error.
    """
    resp = requests.post(
        "https://api.weixin.qq.com/cgi-bin/media/uploadimg",
        params={"access_token": access_token},
        files={"media": (filename, data, _content_type_for(filename))},
        timeout=API_TIMEOUT,
    )
    result = resp.json()

    if "url" not in result:
        errcode = result.get("errcode", "unknown")
        errmsg = result.get("errmsg", "unknown error")
        raise ValueError(f"WeChat upload_image error: errcode={errcode}, errmsg={errmsg}")

    return result["url"]


def upload_thumb_bytes(access_token: str, data: bytes, filename: str = "cover.jpg") -> str:
    """
    Upload a cover image as permanent material (needed as article thumb).
    API: POST https://api.weixin.qq.com/cgi-bin/material/add_material
    Returns media_id string. Raise ValueError on error.
    """
    resp = requests.post(
        "https://api.weixin.qq.com/cgi-bin/material/add_material",
        params={"access_token": access_token, "type": "image"},
        files={"media": (filename, data, _content_type_for(filename))},
        timeout=API_TIMEOUT,
    )
    result = resp.json()

    if "media_id" not in result:
        errcode = result.get("errcode", "unknown")
        errmsg = result.get("errmsg", "unknown error")
        raise ValueError(f"WeChat upload_thumb error: errcode={errcode}, errmsg={errmsg}")

    return result["media_id"]
