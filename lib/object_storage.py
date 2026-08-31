"""S3-compatible object storage for generated images."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote

import boto3
from botocore.config import Config
from PIL import Image


class ObjectStorageError(RuntimeError):
    """Object storage is not configured or an upload failed."""


_IMAGE_EXTENSIONS = {
    "BMP": "bmp",
    "GIF": "gif",
    "JPEG": "jpg",
    "PNG": "png",
    "TIFF": "tiff",
    "WEBP": "webp",
}


class S3ImageStorage:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        cdn_url: str,
        region: str = "auto",
        key_prefix: str = "",
    ):
        missing = [
            name
            for name, value in {
                "R2_ENDPOINT": endpoint,
                "R2_BUCKET": bucket,
                "R2_ACCESS_KEY_ID": access_key_id,
                "R2_SECRET_ACCESS_KEY": secret_access_key,
                "R2_CDN_URL": cdn_url,
            }.items()
            if not value
        ]
        if missing:
            raise ObjectStorageError(f"missing object storage configuration: {', '.join(missing)}")

        self.bucket = bucket
        self.cdn_url = cdn_url.rstrip("/")
        self.key_prefix = key_prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint.rstrip("/"),
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )

    @classmethod
    def from_env(cls) -> "S3ImageStorage":
        return cls(
            endpoint=os.getenv("R2_ENDPOINT", ""),
            bucket=os.getenv("R2_BUCKET", ""),
            access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
            secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
            cdn_url=os.getenv("R2_CDN_URL", ""),
            region=os.getenv("R2_REGION", "auto"),
            key_prefix=os.getenv("R2_KEY_PREFIX", ""),
        )

    def upload_image(self, image_bytes: bytes) -> str:
        extension, content_type = _detect_image_type(image_bytes)
        date_path = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        filename = f"{uuid.uuid4()}.{extension}"
        key_parts = [part for part in (self.key_prefix, date_path, filename) if part]
        key = "/".join(key_parts)

        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=image_bytes,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
        except Exception as exc:
            raise ObjectStorageError(f"failed to upload generated image: {exc}") from exc

        return f"{self.cdn_url}/{quote(key, safe='/')}"


def upload_generated_image(image_bytes: bytes) -> str:
    return S3ImageStorage.from_env().upload_image(image_bytes)


def _detect_image_type(image_bytes: bytes) -> tuple[str, str]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image_format = (image.format or "").upper()
            image.verify()
    except Exception as exc:
        raise ObjectStorageError("generated data is not a valid image") from exc

    extension = _IMAGE_EXTENSIONS.get(image_format)
    content_type = Image.MIME.get(image_format)
    if not extension or not content_type:
        raise ObjectStorageError(f"unsupported generated image format: {image_format or 'unknown'}")
    return extension, content_type
