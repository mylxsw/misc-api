"""Shared bearer-token auth for HTTP REST and Streamable HTTP MCP."""

from __future__ import annotations

import hmac
import os
from typing import Mapping

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

AUTH_ENV = "API_AUTH_TOKEN"
PUBLIC_PATHS = frozenset({"/health"})


def configured_api_token() -> str | None:
    value = (os.getenv(AUTH_ENV) or "").strip()
    return value or None


def request_token(headers: Mapping[str, str]) -> str | None:
    """Read a token from Authorization: Bearer or X-API-Key (case-insensitive)."""
    authorization = _header(headers, "authorization")
    if authorization:
        scheme, _, remainder = authorization.partition(" ")
        if scheme.lower() == "bearer" and remainder.strip():
            return remainder.strip()
    api_key = _header(headers, "x-api-key")
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def token_is_valid(provided: str | None, expected: str) -> bool:
    if provided is None:
        return False
    return hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


def is_public_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return path in PUBLIC_PATHS or normalized in PUBLIC_PATHS


def unauthorized_payload() -> dict[str, str]:
    return {"error": "unauthorized"}


def authorization_headers() -> dict[str, str]:
    """Headers MCP in-process dispatch uses so Flask before_request accepts the call."""
    token = configured_api_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


class BearerTokenMiddleware:
    """Require API_AUTH_TOKEN on every HTTP path except /health when the env var is set."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        expected = configured_api_token()
        path = scope.get("path") or ""
        if expected is None or is_public_path(path):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if token_is_valid(request_token(headers), expected):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            unauthorized_payload(),
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None
