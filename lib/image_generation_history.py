"""Best-effort background persistence for generated-image history."""

from __future__ import annotations

import base64
import logging
import os
import queue
import threading
import time
from datetime import UTC, datetime
from typing import NamedTuple
from urllib.parse import urljoin

import requests
from requests.exceptions import InvalidSchema, InvalidURL, MissingSchema

logger = logging.getLogger(__name__)

HISTORY_PATH = "api/v1/image-generation/history"
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = (5, 25)
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
WORKER_COUNT = 2
QUEUE_CAPACITY = 4
MAX_HISTORY_IMAGE_BYTES = 25 * 1024 * 1024


class _HistoryJob(NamedTuple):
    base_url: str
    api_key: str
    generation_id: str
    image: bytes
    provider: str
    model: str
    prompt: str
    size: str | None
    generated_at: str


_history_queue: queue.Queue[_HistoryJob] = queue.Queue(maxsize=QUEUE_CAPACITY)
_workers_lock = threading.Lock()
_worker_threads: list[threading.Thread] = []


def save_image_history_async(
    *,
    generation_id: str,
    image: bytes,
    provider: str,
    model: str,
    prompt: str,
    size: str | None = None,
) -> None:
    """Enqueue history persistence without encoding or network I/O in the caller."""
    base_url = os.getenv("IMAGE_HISTORY_API_BASE_URL", "").strip()
    if not base_url:
        return

    if len(image) > MAX_HISTORY_IMAGE_BYTES:
        logger.error(
            "Image exceeds history API 25 MB limit; skipping generation_id=%s",
            generation_id,
        )
        return

    api_key = os.getenv("IMAGE_HISTORY_API_KEY", "").strip()
    if not api_key:
        logger.error("IMAGE_HISTORY_API_KEY is not configured; skipping image history")
        return

    try:
        if not _ensure_workers_started():
            logger.error(
                "Failed to start image history workers generation_id=%s",
                generation_id,
            )
            return
        _history_queue.put_nowait(
            _HistoryJob(
                base_url=base_url,
                api_key=api_key,
                generation_id=generation_id,
                image=image,
                provider=provider,
                model=model,
                prompt=prompt,
                size=size,
                generated_at=datetime.now(UTC).isoformat(),
            )
        )
    except queue.Full:
        logger.error(
            "Image history queue is full; skipping generation_id=%s",
            generation_id,
        )
    except Exception:
        # History persistence is best-effort and must never fail image generation.
        logger.exception(
            "Failed to enqueue image generation history generation_id=%s",
            generation_id,
        )


def _ensure_workers_started() -> bool:
    with _workers_lock:
        _worker_threads[:] = [thread for thread in _worker_threads if thread.is_alive()]
        missing = WORKER_COUNT - len(_worker_threads)
        for _ in range(missing):
            try:
                thread = threading.Thread(
                    target=_history_worker,
                    daemon=True,
                    name=f"image-history-worker-{len(_worker_threads) + 1}",
                )
                thread.start()
                _worker_threads.append(thread)
            except Exception:
                logger.exception("Failed to start an image history worker")
                break
        return bool(_worker_threads)


def _history_worker() -> None:
    while True:
        job = _history_queue.get()
        try:
            payload: dict[str, object] = {
                "imageBase64": base64.b64encode(job.image).decode("ascii"),
                "provider": job.provider,
                "model": job.model,
                "prompt": job.prompt,
                "generatedAt": job.generated_at,
            }
            if job.size:
                payload["size"] = job.size
            _save_image_history(
                base_url=job.base_url,
                api_key=job.api_key,
                generation_id=job.generation_id,
                payload=payload,
            )
        except Exception:
            logger.exception(
                "Unexpected image history worker failure generation_id=%s",
                job.generation_id,
            )
        finally:
            _history_queue.task_done()


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
    attempts_made = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts_made = attempt
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
            status_code = exc.response.status_code if exc.response is not None else None
            invalid_request = isinstance(
                exc,
                (InvalidSchema, InvalidURL, MissingSchema),
            )
            retryable = not invalid_request and (
                status_code is None or status_code in RETRYABLE_STATUS_CODES
            )
            if not retryable:
                break
            if attempt < MAX_ATTEMPTS:
                time.sleep(2.0 ** (attempt - 1))

    logger.error(
        "Failed to save image generation history after %d attempts generation_id=%s: %s",
        attempts_made,
        generation_id,
        last_error,
    )
