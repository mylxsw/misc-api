import base64
import unittest
from unittest.mock import Mock, patch

from server import app


class ImageAPIModelsTests(unittest.TestCase):
    def test_models_endpoint_returns_catalog(self):
        with app.test_client() as client:
            response = client.get("/v1/images/models")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["updated_at"], "2026-08-31")
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


if __name__ == "__main__":
    unittest.main()
