import base64
import unittest
from unittest.mock import Mock, patch

from lib.image_generation import ImageGenerationError
from server import app

INPUT_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ImageAPIModelsTests(unittest.TestCase):
    def test_models_endpoint_returns_catalog(self):
        with app.test_client() as client:
            response = client.get("/v1/images/models")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["updated_at"], "2026-09-01")
        self.assertEqual(len(body["providers"]), 6)
        self.assertTrue(all(item["models"] for item in body["providers"]))

    @patch("server.generate_image", return_value=b"image-bytes")
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

    @patch("server.generate_image", return_value=b"image-bytes")
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
        )

    @patch("server.generate_image", return_value=b"image-bytes")
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
            })

        self.assertEqual(response.status_code, 202)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(
            worker_args[5],
            [f"data:image/png;base64,{INPUT_IMAGE_BASE64}"],
        )
        thread_class.return_value.start.assert_called_once_with()

    @patch("server.generate_image", side_effect=ImageGenerationError("provider detail"))
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
