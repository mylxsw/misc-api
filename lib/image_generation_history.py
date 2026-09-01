"""Best-effort background persistence for generated-image history."""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from datetime import UTC, datetime
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

HISTORY_PATH = "api/v1/image-generation/history"
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 30


def save_image_history_async(
    *,
    generation_id: str,
    image: bytes,
    provider: str,
    model: str,
    prompt: str,
    size: str | None = None,
) -> None:
    """Schedule history persistence without delaying the caller."""
    base_url = os.getenv("IMAGE_HISTORY_API_BASE_URL", "").strip()
    if not base_url:
        return

    api_key = os.getenv("IMAGE_HISTORY_API_KEY", "").strip()
    payload: dict[str, object] = {
        "imageBase64": base64.b64encode(image).decode("ascii"),
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "generatedAt": datetime.now(UTC).isoformat(),
    }
    if size:
        payload["size"] = size

    threading.Thread(
        target=_save_image_history,
        kwargs={
            "base_url": base_url,
            "api_key": api_key,
            "generation_id": generation_id,
            "payload": payload,
        },
        daemon=True,
        name=f"image-history-{generation_id}",
    ).start()


def _save_image_history(
    *,
    base_url: str,
    api_key: str,
    generation_id: str,
    payload: dict[str, object],
) -> None:
    url = urljoin(f"{base_url.rstrip('/')}/", HISTORY_PATH)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": f"image-import:{generation_id}",
    }
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                delay_seconds = float(2 ** (attempt - 1))
                time.sleep(delay_seconds)

    logger.error(
        "Failed to save image generation history after %d attempts generation_id=%s: %s",
        MAX_ATTEMPTS,
        generation_id,
        last_error,
    )
