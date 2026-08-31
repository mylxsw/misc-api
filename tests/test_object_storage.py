import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from PIL import Image

from lib.object_storage import ObjectStorageError, S3ImageStorage


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class ObjectStorageTests(unittest.TestCase):
    @patch("lib.object_storage.boto3.client")
    @patch("lib.object_storage.uuid.uuid4", return_value="generated-uuid")
    @patch("lib.object_storage.datetime")
    def test_upload_uses_dated_key_and_cdn_url(self, mock_datetime, _uuid, mock_client):
        mock_datetime.now.return_value.strftime.return_value = "2026/08/31"
        s3 = Mock()
        mock_client.return_value = s3
        storage = S3ImageStorage(
            endpoint="https://r2.example",
            bucket="bucket",
            access_key_id="access",
            secret_access_key="secret",
            cdn_url="https://cdn.example/",
            region="apac",
            key_prefix="misc-resources/",
        )

        url = storage.upload_image(png_bytes())

        self.assertEqual(
            url,
            "https://cdn.example/misc-resources/2026/08/31/generated-uuid.png",
        )
        upload = s3.put_object.call_args.kwargs
        self.assertEqual(upload["Bucket"], "bucket")
        self.assertEqual(
            upload["Key"],
            "misc-resources/2026/08/31/generated-uuid.png",
        )
        self.assertEqual(upload["ContentType"], "image/png")

    @patch("lib.object_storage.boto3.client")
    def test_invalid_image_is_rejected(self, _client):
        storage = S3ImageStorage(
            endpoint="https://r2.example",
            bucket="bucket",
            access_key_id="access",
            secret_access_key="secret",
            cdn_url="https://cdn.example",
        )
        with self.assertRaisesRegex(ObjectStorageError, "not a valid image"):
            storage.upload_image(b"not-an-image")

    def test_missing_configuration_is_rejected(self):
        with self.assertRaisesRegex(ObjectStorageError, "R2_ENDPOINT"):
            S3ImageStorage("", "", "", "", "")


if __name__ == "__main__":
    unittest.main()
