"""Run the MCP server over stdio or Streamable HTTP."""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from lib.mcp.server import mcp, mcp_http_max_body_size

logger = logging.getLogger("misc-api")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expose misc-api HTTP endpoints as MCP tools."
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio for local MCP clients; streamable-http for remote access",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--path",
        default="/mcp",
        help="Streamable HTTP path (only used with --transport streamable-http)",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from lib.auth import AUTH_ENV, BearerTokenMiddleware, configured_api_token

    mcp_app = mcp.streamable_http_app(
        streamable_http_path=args.path,
        stateless_http=True,
        host=args.host,
        max_request_body_size=mcp_http_max_body_size(),
    )

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "http": False, "mcp": True})

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        if configured_api_token() is None:
            logger.warning(
                "%s is not set; MCP HTTP endpoint is unauthenticated",
                AUTH_ENV,
            )
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[Route("/health", health, methods=["GET"]), *mcp_app.routes],
        lifespan=lifespan,
        middleware=[Middleware(BearerTokenMiddleware)],
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
