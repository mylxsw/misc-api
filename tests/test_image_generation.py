import base64
import os
import unittest
from unittest.mock import Mock, patch

from lib.image_generation import (
    APIMartProvider,
    AliyunProvider,
    ArkProvider,
    GeminiProvider,
    ImageGenerationError,
    ToAPIsProvider,
    XAIProvider,
    _content_length,
    _pinned_image_request,
    get_image_model_catalog,
    get_provider,
    normalize_input_images,
)


IMAGE = b"fake-image"
INPUT_IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
INPUT_DATA_URI = f"data:image/png;base64,{base64.b64encode(INPUT_IMAGE).decode()}"


class ImageProviderTests(unittest.TestCase):
    def test_ark_requests_base64_and_maps_ratio(self):
        provider = ArkProvider("key", "https://ark.example/api/v3")
        provider._request_json = Mock(return_value={
            "data": [{"b64_json": base64.b64encode(IMAGE).decode()}]
        })

        self.assertEqual(
            provider.generate("doubao-seedream-5-0-lite-260128", "cat", "16:9"),
            IMAGE,
        )
        call = provider._request_json.call_args
        self.assertEqual(call.args[1], "https://ark.example/api/v3/images/generations")
        self.assertEqual(call.kwargs["json"]["size"], "2848x1600")
        self.assertEqual(call.kwargs["json"]["response_format"], "b64_json")
        self.assertFalse(call.kwargs["json"]["watermark"])

    def test_ark_passes_reference_images(self):
        provider = ArkProvider("key", "https://ark.example/api/v3")
        provider._request_json = Mock(return_value={
            "data": [{"b64_json": base64.b64encode(IMAGE).decode()}]
        })

        provider.generate(
            "doubao-seedream-4-5-251128",
            "use both references",
            None,
            ["https://example.com/a.png", INPUT_DATA_URI],
        )

        payload = provider._request_json.call_args.kwargs["json"]
        self.assertEqual(
            payload["image"],
            ["https://example.com/a.png", INPUT_DATA_URI],
        )

    def test_aliyun_qwen_uses_sync_api_and_maps_ratio(self):
        provider = AliyunProvider("key", "https://aliyun.example/api/v1")
        provider._request_json = Mock(return_value={
            "output": {"choices": [{"message": {"content": [
                {"image": "https://image.example/qwen.png"}
            ]}}]}
        })
        provider._download_image = Mock(return_value=IMAGE)

        self.assertEqual(provider.generate("qwen-image-3.0-pro", "cat", "16:9"), IMAGE)
        call = provider._request_json.call_args
        self.assertIn("multimodal-generation", call.args[1])
        self.assertEqual(call.kwargs["json"]["parameters"]["size"], "1280*720")
        self.assertNotIn("X-DashScope-Async", call.kwargs["headers"])

    def test_aliyun_adds_reference_images_before_prompt(self):
        provider = AliyunProvider("key", "https://aliyun.example/api/v1")
        provider._request_json = Mock(return_value={
            "output": {"choices": [{"message": {"content": [
                {"image": "https://image.example/qwen.png"}
            ]}}]}
        })
        provider._download_image = Mock(return_value=IMAGE)

        provider.generate(
            "qwen-image-3.0-pro",
            "combine them",
            None,
            ["https://example.com/a.png", INPUT_DATA_URI],
        )

        content = provider._request_json.call_args.kwargs["json"]["input"]["messages"][0]["content"]
        self.assertEqual(content[0], {"image": "https://example.com/a.png"})
        self.assertEqual(content[1], {"image": INPUT_DATA_URI})
        self.assertEqual(content[2], {"text": "combine them"})

    def test_aliyun_text_only_models_reject_reference_images(self):
        provider = AliyunProvider("key", "https://aliyun.example/api/v1")
        for model in ("z-image-turbo", "wan2.5-t2i-preview"):
            with self.subTest(model=model):
                with self.assertRaisesRegex(ImageGenerationError, "does not support input images"):
                    provider.generate(model, "edit", None, [INPUT_DATA_URI])

    def test_aliyun_z_image_uses_sync_api(self):
        provider = AliyunProvider("key", "https://aliyun.example/api/v1")
        provider._request_json = Mock(return_value={
            "output": {"choices": [{"message": {"content": [
                {"image": "https://image.example/z.png"}
            ]}}]}
        })
        provider._download_image = Mock(return_value=IMAGE)

        self.assertEqual(provider.generate("z-image-turbo", "cat", "1K"), IMAGE)
        payload = provider._request_json.call_args.kwargs["json"]
        self.assertEqual(payload["parameters"]["size"], "1024*1024")

    @patch("lib.image_generation.time.sleep")
    def test_aliyun_wan_submits_and_polls(self, _sleep):
        provider = AliyunProvider("key", "https://aliyun.example/api/v1")
        provider._request_json = Mock(side_effect=[
            {"output": {"task_id": "task-wan", "task_status": "PENDING"}},
            {"output": {
                "task_status": "SUCCEEDED",
                "choices": [{"message": {"content": [
                    {"image": "https://image.example/wan.png"}
                ]}}],
            }},
        ])
        provider._download_image = Mock(return_value=IMAGE)

        self.assertEqual(provider.generate("wan2.7-image-pro", "cat", "16:9"), IMAGE)
        submit = provider._request_json.call_args_list[0]
        self.assertIn("image-generation", submit.args[1])
        self.assertEqual(submit.kwargs["headers"]["X-DashScope-Async"], "enable")
        self.assertEqual(submit.kwargs["json"]["parameters"]["size"], "2688*1536")
        poll = provider._request_json.call_args_list[1]
        self.assertTrue(poll.args[1].endswith("/tasks/task-wan"))

    def test_gemini_maps_ratio_and_reads_inline_image(self):
        provider = GeminiProvider("key", "https://gemini.example")
        provider._request_json = Mock(return_value={
            "candidates": [{"content": {"parts": [{
                "inlineData": {"data": base64.b64encode(IMAGE).decode()}
            }]}}]
        })

        self.assertEqual(provider.generate("image-model", "cat", "16:9"), IMAGE)
        call = provider._request_json.call_args
        self.assertEqual(
            call.args[1],
            "https://gemini.example/v1/models/image-model:generateContent",
        )
        self.assertEqual(call.kwargs["headers"], {"x-goog-api-key": "key"})
        self.assertNotIn("params", call.kwargs)
        payload = call.kwargs["json"]
        self.assertEqual(
            payload["generationConfig"]["responseFormat"],
            {"image": {"aspectRatio": "16:9"}},
        )

    def test_gemini_maps_resolution_to_current_rest_response_format(self):
        provider = GeminiProvider("key", "https://gemini.example")
        provider._request_json = Mock(return_value={
            "candidates": [{"content": {"parts": [{
                "inlineData": {"data": base64.b64encode(IMAGE).decode()}
            }]}}]
        })

        provider.generate("image-model", "cat", "2K")

        config = provider._request_json.call_args.kwargs["json"]["generationConfig"]
        self.assertEqual(config["responseFormat"], {"image": {"imageSize": "2K"}})
        self.assertNotIn("imageConfig", config)

    def test_gemini_sends_reference_image_as_inline_data(self):
        provider = GeminiProvider("key", "https://gemini.example")
        provider._request_json = Mock(return_value={
            "candidates": [{"content": {"parts": [{
                "inlineData": {"data": base64.b64encode(IMAGE).decode()}
            }]}}]
        })

        self.assertEqual(
            provider.generate("image-model", "make it blue", None, [INPUT_DATA_URI]),
            IMAGE,
        )
        parts = provider._request_json.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertIn("provided reference image", parts[0]["text"])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(parts[1]["inline_data"]["data"], base64.b64encode(INPUT_IMAGE).decode())

    def test_gemini_rejects_private_input_image_url(self):
        provider = GeminiProvider("key", "https://gemini.example")
        with self.assertRaisesRegex(ImageGenerationError, "public address"):
            provider.generate("image-model", "edit", None, ["http://127.0.0.1/a.png"])

    def test_xai_maps_resolution_and_reads_base64(self):
        provider = XAIProvider("key", "https://xai.example/v1")
        provider._request_json = Mock(return_value={
            "data": [{"b64_json": base64.b64encode(IMAGE).decode()}]
        })

        self.assertEqual(provider.generate("grok-image", "cat", "2k"), IMAGE)
        payload = provider._request_json.call_args.kwargs["json"]
        self.assertEqual(payload["resolution"], "2k")
        self.assertEqual(payload["response_format"], "b64_json")
        self.assertNotIn("aspect_ratio", payload)

    def test_xai_uses_json_edit_endpoint_for_reference_images(self):
        provider = XAIProvider("key", "https://xai.example/v1")
        provider._request_json = Mock(return_value={
            "data": [{"b64_json": base64.b64encode(IMAGE).decode()}]
        })
        images = ["https://example.com/a.png", INPUT_DATA_URI]

        provider.generate("grok-imagine-image-2.0", "merge", "3:2", images)

        call = provider._request_json.call_args
        self.assertEqual(call.args[1], "https://xai.example/v1/images/edits")
        self.assertEqual(
            call.kwargs["json"]["images"],
            [{"type": "image_url", "url": image} for image in images],
        )
        self.assertEqual(call.kwargs["json"]["aspect_ratio"], "3:2")
        self.assertEqual(call.kwargs["json"]["response_format"], "b64_json")

    @patch("lib.image_generation.time.sleep")
    def test_apimart_submits_and_polls(self, _sleep):
        provider = APIMartProvider("key", "https://apimart.example/v1")
        provider._request_json = Mock(side_effect=[
            {"code": 200, "data": [{"task_id": "task-1"}]},
            {"code": 200, "data": {
                "status": "completed",
                "result": {"images": [{"url": ["https://image.example/a.png"]}]},
            }},
        ])
        provider._download_image = Mock(return_value=IMAGE)

        self.assertEqual(
            provider.generate("gpt-image-2", "cat", "1:1", [INPUT_DATA_URI]),
            IMAGE,
        )
        submit = provider._request_json.call_args_list[0]
        self.assertEqual(submit.kwargs["json"]["size"], "1:1")
        self.assertEqual(submit.kwargs["json"]["image_urls"], [INPUT_DATA_URI])
        self.assertEqual(provider._download_image.call_args.args[0], "https://image.example/a.png")

    @patch("lib.image_generation.time.sleep")
    def test_toapis_submits_and_polls(self, _sleep):
        provider = ToAPIsProvider("key", "https://toapis.example/v1")
        provider._request_json = Mock(side_effect=[
            {"id": "task-2", "status": "queued"},
            {"id": "task-2", "status": "completed", "result": {
                "data": [{"url": "https://image.example/b.png"}]
            }},
        ])
        provider._download_image = Mock(return_value=IMAGE)

        self.assertEqual(provider.generate("gemini-model", "cat", None), IMAGE)
        submit = provider._request_json.call_args_list[0]
        self.assertNotIn("resolution", submit.kwargs["json"])
        self.assertNotIn("size", submit.kwargs["json"])

    @patch("lib.image_generation.time.sleep")
    def test_toapis_uses_image_urls_field(self, _sleep):
        provider = ToAPIsProvider("key", "https://toapis.example/v1")
        provider._request_json = Mock(side_effect=[
            {"id": "task-3", "status": "queued"},
            {"id": "task-3", "status": "completed", "result": {
                "data": [{"url": "https://image.example/c.png"}]
            }},
        ])
        provider._download_image = Mock(return_value=IMAGE)

        provider.generate(
            "gemini-model",
            "edit",
            "1:1",
            ["https://example.com/reference.png"],
        )

        submit = provider._request_json.call_args_list[0]
        self.assertEqual(
            submit.kwargs["json"]["image_urls"],
            ["https://example.com/reference.png"],
        )

    def test_toapis_rejects_base64_reference_images(self):
        provider = ToAPIsProvider("key", "https://toapis.example/v1")
        with self.assertRaisesRegex(ImageGenerationError, "public http"):
            provider.generate("gemini-model", "edit", None, [INPUT_DATA_URI])

    def test_normalize_input_images_accepts_bare_base64(self):
        normalized = normalize_input_images([base64.b64encode(INPUT_IMAGE).decode()])
        self.assertEqual(normalized, [INPUT_DATA_URI])

    def test_normalize_input_images_rejects_more_than_three(self):
        with self.assertRaisesRegex(ImageGenerationError, "at most 3"):
            normalize_input_images([INPUT_DATA_URI] * 4)

    def test_normalize_input_images_uses_aliyun_qwen_10_mb_limit(self):
        oversized = base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024)
        ).decode()
        with self.assertRaisesRegex(ImageGenerationError, "10 MB"):
            normalize_input_images(
                [oversized], provider="aliyun", model="qwen-image-3.0-pro"
            )

    def test_normalize_input_images_rejects_non_portable_format(self):
        bmp = base64.b64encode(b"BM" + b"x" * 32).decode()
        with self.assertRaisesRegex(ImageGenerationError, "unsupported format"):
            normalize_input_images([bmp], provider="gemini", model="image-model")

    @patch("lib.image_generation.HTTPSConnectionPool")
    @patch("lib.image_generation.socket.getaddrinfo")
    def test_pinned_image_request_connects_to_validated_ip_with_original_tls_name(
        self, getaddrinfo, pool_class
    ):
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        response = Mock()
        pool_class.return_value.request.return_value = response

        actual, pool = _pinned_image_request("https://example.com/image.png?x=1", "gemini")

        self.assertIs(actual, response)
        self.assertIs(pool, pool_class.return_value)
        pool_class.assert_called_once_with(
            host="93.184.216.34",
            port=443,
            timeout=120,
            retries=False,
            assert_hostname="example.com",
            cert_reqs="CERT_REQUIRED",
            server_hostname="example.com",
        )
        pool_class.return_value.request.assert_called_once_with(
            "GET",
            "/image.png?x=1",
            headers={"Host": "example.com", "Accept": "image/*"},
            preload_content=False,
            redirect=False,
        )

    def test_invalid_content_length_is_reported_as_provider_error(self):
        with self.assertRaisesRegex(ImageGenerationError, "invalid Content-Length"):
            _content_length({"Content-Length": "not-a-number"}, "gemini")

    def test_model_catalog_covers_every_provider(self):
        catalog = get_image_model_catalog()
        self.assertEqual(catalog["updated_at"], "2026-09-01")
        providers = {item["provider"]: item for item in catalog["providers"]}
        self.assertEqual(
            set(providers),
            {"aliyun", "ark", "apimart", "toapis", "gemini", "xai"},
        )
        self.assertIn("qwen-image-3.0-pro", providers["aliyun"]["models"])
        self.assertIn("gpt-image-2", providers["apimart"]["models"])
        self.assertEqual(
            providers["ark"]["models"][:3],
            [
                "doubao-seedream-5-0-260128",
                "doubao-seedream-5-0-lite-260128",
                "doubao-seedream-4-5-251128",
            ],
        )
        self.assertIn("grok-imagine-image-2.0", providers["xai"]["models"])

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported provider"):
            get_provider("unknown")

    def test_missing_key_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ImageGenerationError, "API key is not configured"):
                get_provider("apimart")


if __name__ == "__main__":
    unittest.main()
