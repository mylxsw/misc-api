"""WeChat Official Account media/token API helpers.

Adapted from wewrite (toolkit/wechat_api.py) for a stateless HTTP service:
images are handled as in-memory bytes (sourced from URLs or base64) rather
than local file paths.
"""

import base64
import binascii
import json
import mimetypes
import time
from dataclasses import dataclass

import requests

# Token cache keyed by appid
_token_cache: dict = {}

# Unified timeout for WeChat API calls
API_TIMEOUT = 30

# Token endpoints. The stable-token API returns the same token for repeated
# calls, while the classic endpoint rotates it on every call.
STABLE_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/stable_token"
CLASSIC_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"

_JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8"}

# errcodes that describe unusable credentials, where retrying the other token
# endpoint cannot help.
_CREDENTIAL_ERRCODES = {40013, 40125, 40164, 41002}


@dataclass
class TokenResult:
    access_token: str
    expires_at: float  # unix timestamp


def _fetch_token_data(
    appid: str, secret: str, force_refresh: bool = False
) -> tuple[dict | None, str | None]:
    """Return `(data, error)` for the account's access token.

    Prefer WeChat's stable-token API: the classic `/cgi-bin/token` endpoint
    issues a new token on every call and invalidates the previous one, so two
    gunicorn workers that cache tokens independently keep killing each other's
    token and later media calls fail with `errcode=40001`. The stable endpoint
    returns the same token until it expires, which is safe for multiple workers.
    """
    stable_error: str | None = None
    try:
        stable = requests.post(
            STABLE_TOKEN_URL,
            data=json.dumps(
                {
                    "grant_type": "client_credential",
                    "appid": appid,
                    "secret": secret,
                    "force_refresh": bool(force_refresh),
                }
            ).encode("utf-8"),
            headers=_JSON_HEADERS,
            timeout=API_TIMEOUT,
        ).json()
    except (requests.RequestException, ValueError):
        stable = None

    if isinstance(stable, dict) and "access_token" in stable:
        return stable, None
    if isinstance(stable, dict):
        errcode = stable.get("errcode", "unknown")
        errmsg = stable.get("errmsg", "unknown error")
        if errcode in _CREDENTIAL_ERRCODES:
            return None, f"WeChat API error: errcode={errcode}, errmsg={errmsg}"
        stable_error = f"errcode={errcode}, errmsg={errmsg}"

    classic = requests.get(
        CLASSIC_TOKEN_URL,
        params={
            "grant_type": "client_credential",
            "appid": appid,
            "secret": secret,
        },
        timeout=API_TIMEOUT,
    ).json()

    if "access_token" in classic:
        return classic, None

    errcode = classic.get("errcode", "unknown")
    errmsg = classic.get("errmsg", "unknown error")
    detail = f"errcode={errcode}, errmsg={errmsg}"
    if stable_error:
        detail = f"{detail} (stable_token returned {stable_error})"
    return None, f"WeChat API error: {detail}"


def get_access_token(appid: str, secret: str, force_refresh: bool = False) -> str:
    """
    Get access_token with caching and auto-refresh.
    Cache key: appid. Cache until expires_in - 300 seconds (5 min buffer).
    API: POST https://api.weixin.qq.com/cgi-bin/stable_token, with the classic
    GET https://api.weixin.qq.com/cgi-bin/token as fallback.
    Raise ValueError on API error.
    """
    now = time.time()

    if not force_refresh and appid in _token_cache:
        cached: TokenResult = _token_cache[appid]
        if now < cached.expires_at:
            return cached.access_token

    data, error = _fetch_token_data(appid, secret, force_refresh)
    if error is not None or data is None or "access_token" not in data:
        raise ValueError(error or "WeChat API error: unknown error")

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
                raise ValueError(
                    "unsupported data: URI (only base64 payloads are accepted)"
                )
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


def upload_image_bytes(
    access_token: str, data: bytes, filename: str = "image.jpg"
) -> str:
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
        raise ValueError(
            f"WeChat upload_image error: errcode={errcode}, errmsg={errmsg}"
        )

    return result["url"]


def upload_thumb_bytes(
    access_token: str, data: bytes, filename: str = "cover.jpg"
) -> str:
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
        raise ValueError(
            f"WeChat upload_thumb error: errcode={errcode}, errmsg={errmsg}"
        )

    return result["media_id"]
