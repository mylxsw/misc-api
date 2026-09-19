"""ASGI entry point for uvicorn/gunicorn: REST API + MCP at /mcp."""

from lib.mcp.asgi import app

__all__ = ["app"]
