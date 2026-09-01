import base64
import unittest
from unittest.mock import Mock, patch

from lib.image_generation import ImageGenerationError
import server
from server import app

INPUT_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ImageAPIModelsTests(unittest.TestCase):
    def test_request_body_limit_returns_json_413(self):
        previous = app.config["MAX_CONTENT_LENGTH"]
        app.config["MAX_CONTENT_LENGTH"] = 64
        try:
            with app.test_client() as client:
                response = client.post(
                    "/v1/images/generations",
                    data=b"{" + b"x" * 128 + b"}",
                    content_type="application/json",
                )
        finally:
            app.config["MAX_CONTENT_LENGTH"] = previous

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.get_json(),
            {"error": "request body exceeds the configured size limit"},
        )

    def test_models_endpoint_returns_catalog(self):
        with app.test_client() as client:
            response = client.get("/v1/images/models")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["updated_at"], "2026-09-01")
        self.assertEqual(len(body["providers"]), 6)
        self.assertTrue(all(item["models"] for item in body["providers"]))

    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_generation_defaults_to_base64(self, _generate):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "xai",
                "model": "model",
                "prompt": "cat",
            })

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["image_base64"], base64.b64encode(b"image-bytes").decode())
        self.assertNotIn("image_url", body)

    @patch("server.save_image_history_async")
    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_generation_does_not_record_history_by_default(self, _generate, save_history):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "xai",
                "model": "model",
                "prompt": "cat",
            })

        self.assertEqual(response.status_code, 200)
        save_history.assert_not_called()

    @patch("server.save_image_history_async")
    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_generation_records_history_only_when_enabled(self, _generate, save_history):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "xai",
                "model": "model",
                "prompt": "cat",
                "aspect_ratio": "16:9",
                "resolution": "2K",
                "record_history": True,
            })

        self.assertEqual(response.status_code, 200)
        save_history.assert_called_once()
        self.assertEqual(save_history.call_args.kwargs["image"], b"image-bytes")
        self.assertEqual(save_history.call_args.kwargs["prompt"], "cat")
        self.assertEqual(save_history.call_args.kwargs["size"], "16:9 @ 2K")

    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_generation_passes_normalized_input_images(self, generate):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "ark",
                "model": "model",
                "prompt": "make it blue",
                "images": [INPUT_IMAGE_BASE64],
            })

        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with(
            provider="ark",
            model="model",
            prompt="make it blue",
            size=None,
            images=[f"data:image/png;base64,{INPUT_IMAGE_BASE64}"],
            aspect_ratio=None,
            resolution=None,
        )

    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_generation_passes_new_size_fields_and_keeps_legacy_size(self, generate):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "xai",
                "model": "model",
                "prompt": "cat",
                "size": "1:1",
                "aspect_ratio": "16:9",
                "resolution": "2K",
            })

        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with(
            provider="xai",
            model="model",
            prompt="cat",
            size="1:1",
            images=[],
            aspect_ratio="16:9",
            resolution="2K",
        )

    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    @patch("server.S3ImageStorage.from_env")
    def test_generation_can_return_uploaded_url(self, from_env, _generate):
        storage = Mock()
        storage.upload_image.return_value = "https://cdn.example/2026/08/31/image.png"
        from_env.return_value = storage

        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "ark",
                "model": "model",
                "prompt": "cat",
                "return_url": True,
            })

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["image_url"], "https://cdn.example/2026/08/31/image.png")
        self.assertNotIn("image_base64", body)
        storage.upload_image.assert_called_once_with(b"image-bytes")

    def test_record_history_must_be_boolean(self):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "ark",
                "model": "model",
                "prompt": "cat",
                "record_history": "true",
            })

        self.assertEqual(response.status_code, 400)
        self.assertIn("record_history", response.get_json()["error"])

    def test_return_url_must_be_boolean(self):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "ark",
                "model": "model",
                "prompt": "cat",
                "return_url": "true",
            })

        self.assertEqual(response.status_code, 400)
        self.assertIn("must be a boolean", response.get_json()["error"])

    def test_images_must_be_an_array(self):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "ark",
                "model": "model",
                "prompt": "cat",
                "images": INPUT_IMAGE_BASE64,
            })

        self.assertEqual(response.status_code, 400)
        self.assertIn("must be an array", response.get_json()["error"])

    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_aspect_ratio_is_mapped_for_provider_model(self, generate):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "ark",
                "model": "doubao-seedream-5-0-260128",
                "prompt": "banner",
                "aspect_ratio": "2.35:1",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(generate.call_args.kwargs["aspect_ratio"], "21:9")

    def test_unknown_aspect_ratio_is_rejected(self):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "gemini",
                "model": "gemini-3.1-flash-image",
                "prompt": "cat",
                "aspect_ratio": "7:5",
            })

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported aspect ratio", response.get_json()["error"])

    def test_new_size_fields_must_be_non_empty_strings(self):
        for field, value in (("aspect_ratio", ""), ("resolution", 2048)):
            with self.subTest(field=field):
                with app.test_client() as client:
                    response = client.post("/v1/images/generations", json={
                        "provider": "ark",
                        "model": "model",
                        "prompt": "cat",
                        field: value,
                    })

                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.get_json()["error"])

    @patch("server.threading.Thread")
    @patch("server.redis_client")
    @patch("server.get_provider")
    def test_async_generation_passes_input_images_to_worker(
        self, _get_provider, _redis, thread_class
    ):
        with app.test_client() as client:
            response = client.post("/v1/images/generations/async", json={
                "provider": "ark",
                "model": "model",
                "prompt": "make it blue",
                "images": [INPUT_IMAGE_BASE64],
                "aspect_ratio": "16:9",
                "resolution": "2K",
            })

        self.assertEqual(response.status_code, 202)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(
            worker_args[5],
            [f"data:image/png;base64,{INPUT_IMAGE_BASE64}"],
        )
        self.assertEqual(worker_args[7], "16:9")
        self.assertEqual(worker_args[8], "2K")
        self.assertFalse(worker_args[9])
        thread_class.return_value.start.assert_called_once_with()

    @patch("server.threading.Thread")
    @patch("server.redis_client")
    @patch("server.get_provider")
    def test_async_generation_passes_record_history_to_worker(
        self, _get_provider, _redis, thread_class
    ):
        with app.test_client() as client:
            response = client.post("/v1/images/generations/async", json={
                "provider": "ark",
                "model": "model",
                "prompt": "cat",
                "record_history": True,
            })

        self.assertEqual(response.status_code, 202)
        self.assertTrue(thread_class.call_args.kwargs["args"][9])

    @patch("server.redis_client")
    @patch("server.save_image_history_async")
    @patch("server.generate_normalized_image", return_value=b"image-bytes")
    def test_async_worker_records_with_task_id(
        self, _generate, save_history, _redis
    ):
        server.process_image_generation_task(
            task_id="task-1",
            provider="xai",
            model="model",
            prompt="cat",
            size=None,
            images=[],
            return_url=False,
            record_history=True,
        )

        save_history.assert_called_once()
        self.assertEqual(
            save_history.call_args.kwargs["generation_id"],
            "task-1",
        )

    @patch("server.generate_normalized_image", side_effect=ImageGenerationError("provider detail"))
    def test_provider_error_preserves_json_body_without_using_502(self, _generate):
        with app.test_client() as client:
            response = client.post("/v1/images/generations", json={
                "provider": "xai",
                "model": "model",
                "prompt": "cat",
            })

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.get_json(), {"error": "provider detail"})


if __name__ == "__main__":
    unittest.main()
