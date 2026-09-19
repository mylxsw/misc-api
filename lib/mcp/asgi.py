"""Combined ASGI app: Flask REST API plus Streamable HTTP MCP at /mcp."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a2wsgi import WSGIMiddleware
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from lib.auth import AUTH_ENV, BearerTokenMiddleware, configured_api_token
from lib.mcp.server import mcp, mcp_http_max_body_size

logger = logging.getLogger("misc-api")


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "http": True, "mcp": True})


def create_asgi_app() -> Starlette:
    """Serve existing Flask routes and the MCP endpoint from one process.

    The MCP route is registered at `/mcp` (not nested under a `/mcp` mount)
    so clients that omit the trailing slash still reach Streamable HTTP.
    Starlette then redirects `/mcp/` to `/mcp`. Flask is the catch-all.
    Streamable HTTP is stateless so multiple workers can share the endpoint.
    """
    from server import app as flask_app

    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host="0.0.0.0",
        max_request_body_size=mcp_http_max_body_size(),
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        if configured_api_token() is None:
            logger.warning(
                "%s is not set; HTTP REST and MCP endpoints are unauthenticated",
                AUTH_ENV,
            )
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            *mcp_app.routes,
            Mount("/", app=WSGIMiddleware(flask_app, workers=8)),
        ],
        lifespan=lifespan,
        middleware=[Middleware(BearerTokenMiddleware)],
    )


app = create_asgi_app()
