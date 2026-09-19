import asyncio
import base64
import json
import os
import unittest
from unittest.mock import patch

from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient

from lib.mcp.asgi import create_asgi_app
from lib.mcp.dispatch import invoke_route
from lib.mcp.server import mcp
from server import app as flask_app

EXPECTED_TOOLS = {
    "list_image_models",
    "generate_image",
    "create_image_generation_task",
    "get_image_generation_task",
    "stitch_images",
    "cosyvoice_tts",
    "create_cosyvoice_task",
    "get_cosyvoice_task",
    "create_podcast_task",
    "get_podcast_task",
    "create_fish_audio_task",
    "get_fish_audio_task",
    "list_wechat_themes",
    "preview_wechat_article",
    "publish_wechat_draft",
    "create_wechat_image_draft",
    "list_wechat_drafts",
    "get_wechat_draft",
    "update_wechat_draft",
    "delete_wechat_draft",
}


def _run(coro):
    return asyncio.run(coro)


MCP_INIT_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-03-26",
}

MCP_INIT_BODY = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "tests", "version": "0"},
        },
    }
)


class MCPDispatchTests(unittest.TestCase):
    def test_invoke_route_returns_json(self):
        body = invoke_route("GET", "/v1/images/models")
        self.assertIn("providers", body)
        self.assertIn("updated_at", body)

    def test_invoke_route_raises_tool_error_on_http_error(self):
        with self.assertRaises(ToolError) as raised:
            invoke_route("POST", "/v1/images/generations", json_body={})
        self.assertIn("HTTP 400", str(raised.exception))
        self.assertIn("provider", str(raised.exception))


class MCPToolTests(unittest.TestCase):
    def test_lists_every_http_api_as_a_tool(self):
        async def _list():
            async with Client(mcp) as client:
                result = await client.list_tools()
                return result.tools

        tools = _run(_list())
        names = {tool.name for tool in tools}
        self.assertEqual(names, EXPECTED_TOOLS)

    def test_delete_wechat_draft_is_marked_destructive(self):
        async def _list():
            async with Client(mcp) as client:
                result = await client.list_tools()
                return {tool.name: tool.annotations for tool in result.tools}

        annotations = _run(_list())
        delete = annotations["delete_wechat_draft"]
        self.assertTrue(delete.destructive_hint)
        self.assertFalse(delete.read_only_hint)
        self.assertTrue(annotations["list_image_models"].read_only_hint)

    def test_list_image_models_matches_http(self):
        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool("list_image_models", {})

        with flask_app.test_client() as http:
            http_body = http.get("/v1/images/models").get_json()

        result = _run(_call())
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, http_body)

    def test_list_wechat_themes_matches_http(self):
        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool("list_wechat_themes", {})

        with flask_app.test_client() as http:
            http_body = http.get("/v1/wechat/markdown/themes").get_json()

        result = _run(_call())
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, http_body)

    def test_preview_wechat_article(self):
        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool(
                    "preview_wechat_article",
                    {"markdown": "# 标题\n\n正文", "theme": "sspai"},
                )

        result = _run(_call())
        self.assertFalse(result.is_error)
        body = result.structured_content
        self.assertEqual(body["title"], "标题")
        self.assertEqual(body["theme"], "sspai")
        self.assertIn("<", body["html"])

    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_generate_image_returns_base64(self, _generate):
        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool(
                    "generate_image",
                    {
                        "provider": "xai",
                        "model": "model",
                        "prompt": "cat",
                    },
                )

        result = _run(_call())
        self.assertFalse(result.is_error)
        body = result.structured_content
        self.assertEqual(
            body["image_base64"], base64.b64encode(b"image-bytes").decode()
        )
        self.assertEqual(body["provider"], "xai")

    def test_generate_image_validation_error(self):
        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool(
                    "generate_image",
                    {"provider": "xai", "model": "model", "prompt": ""},
                )

        result = _run(_call())
        self.assertTrue(result.is_error)
        self.assertIn("prompt", result.content[0].text)

    @patch("server.get_access_token", return_value="token")
    @patch("server.list_drafts")
    def test_list_wechat_drafts_uses_headers(self, list_drafts, _token):
        list_drafts.return_value = {"total_count": 0, "item_count": 0, "item": []}

        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool(
                    "list_wechat_drafts",
                    {
                        "offset": 0,
                        "count": 10,
                        "no_content": 1,
                        "appid": "appid",
                        "secret": "secret",
                    },
                )

        result = _run(_call())
        self.assertFalse(result.is_error)
        list_drafts.assert_called_once_with("token", 0, 10, 1)


class CombinedASGITests(unittest.TestCase):
    def test_rest_mcp_and_health_share_the_same_app(self):
        with TestClient(create_asgi_app()) as client:
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json(), {"status": "ok", "http": True, "mcp": True})

            rest = client.get("/v1/images/models")
            self.assertEqual(rest.status_code, 200)
            self.assertIn("providers", rest.json())

            initialize = client.post(
                "/mcp", headers=MCP_INIT_HEADERS, content=MCP_INIT_BODY
            )
            self.assertEqual(initialize.status_code, 200)
            self.assertIn("misc-api", initialize.text)
            self.assertIn("tools", initialize.text)


class APIAuthTests(unittest.TestCase):
    def test_flask_health_is_public(self):
        with flask_app.test_client() as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["http"], True)
        self.assertEqual(response.get_json()["mcp"], False)

    def test_flask_requires_bearer_token_when_configured(self):
        with patch.dict(os.environ, {"API_AUTH_TOKEN": "secret"}):
            with flask_app.test_client() as client:
                denied = client.get("/v1/images/models")
                self.assertEqual(denied.status_code, 401)
                self.assertEqual(denied.get_json(), {"error": "unauthorized"})
                self.assertEqual(denied.headers.get("WWW-Authenticate"), "Bearer")

                allowed = client.get(
                    "/v1/images/models",
                    headers={"Authorization": "Bearer secret"},
                )
                self.assertEqual(allowed.status_code, 200)

                via_key = client.get(
                    "/v1/images/models",
                    headers={"X-API-Key": "secret"},
                )
                self.assertEqual(via_key.status_code, 200)

                wrong = client.get(
                    "/v1/images/models",
                    headers={"Authorization": "Bearer wrong"},
                )
                self.assertEqual(wrong.status_code, 401)

                health = client.get("/health")
                self.assertEqual(health.status_code, 200)

    def test_asgi_requires_token_for_rest_and_mcp(self):
        with patch.dict(os.environ, {"API_AUTH_TOKEN": "secret"}):
            with TestClient(create_asgi_app()) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/v1/images/models").status_code, 401)
                self.assertEqual(
                    client.post(
                        "/mcp", headers=MCP_INIT_HEADERS, content=MCP_INIT_BODY
                    ).status_code,
                    401,
                )

                authed = {"Authorization": "Bearer secret"}
                rest = client.get("/v1/images/models", headers=authed)
                self.assertEqual(rest.status_code, 200)

                initialize = client.post(
                    "/mcp",
                    headers={**MCP_INIT_HEADERS, **authed},
                    content=MCP_INIT_BODY,
                )
                self.assertEqual(initialize.status_code, 200)
                self.assertIn("misc-api", initialize.text)

    def test_mcp_tools_still_work_when_token_is_configured(self):
        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool("list_image_models", {})

        with patch.dict(os.environ, {"API_AUTH_TOKEN": "secret"}):
            result = _run(_call())
        self.assertFalse(result.is_error)
        self.assertIn("providers", result.structured_content)


if __name__ == "__main__":
    unittest.main()
