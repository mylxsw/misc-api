"""In-process dispatch from MCP tools onto the existing Flask routes.

Tools call the same view functions the HTTP API uses, so validation, status
codes, and response bodies stay in lockstep without a second HTTP hop.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from lib.auth import authorization_headers
from mcp.server.mcpserver.exceptions import ToolError


def invoke_route(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    query: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    """Call a Flask route in-process and return its JSON body.

    Raises ToolError when the route returns HTTP 4xx/5xx so MCP clients see
    the same error payload the REST API would have returned.
    """
    from server import app

    kwargs: dict[str, Any] = {}
    if json_body is not None:
        kwargs["json"] = json_body
    if query:
        kwargs["query_string"] = {
            key: value for key, value in query.items() if value is not None
        }
    merged_headers = {**authorization_headers(), **dict(headers or {})}
    if merged_headers:
        kwargs["headers"] = merged_headers

    with app.test_client() as client:
        response = client.open(path, method=method.upper(), **kwargs)

    body = response.get_json(silent=True)
    if response.status_code >= 400:
        payload = (
            body if isinstance(body, dict) else {"error": response.get_data(as_text=True)}
        )
        raise ToolError(_format_http_error(response.status_code, payload))
    if body is None:
        return {
            "status": response.status_code,
            "body": response.get_data(as_text=True),
        }
    return body


def omit_none(data: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None so Flask sees omitted optional fields."""
    return {key: value for key, value in data.items() if value is not None}


def wechat_headers(appid: str | None, secret: str | None) -> dict[str, str] | None:
    """Build the per-request WeChat credential headers used by the REST API."""
    headers: dict[str, str] = {}
    if appid:
        headers["X-WeChat-AppId"] = appid
    if secret:
        headers["X-WeChat-AppSecret"] = secret
    return headers or None


def _format_http_error(status: int, payload: dict[str, Any]) -> str:
    message = payload.get("error")
    extras = {key: value for key, value in payload.items() if key != "error"}
    if message is None:
        return f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)}"
    if extras:
        return f"HTTP {status}: {message} {json.dumps(extras, ensure_ascii=False)}"
    return f"HTTP {status}: {message}"
