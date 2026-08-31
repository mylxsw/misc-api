import unittest

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


if __name__ == "__main__":
    unittest.main()
