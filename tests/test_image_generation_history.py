import unittest
from unittest.mock import Mock, patch

import requests

from lib.image_generation_history import (
    MAX_ATTEMPTS,
    _save_image_history,
    save_image_history_async,
)


class ImageGenerationHistoryTests(unittest.TestCase):
    @patch("lib.image_generation_history._ensure_workers_started")
    @patch.dict("os.environ", {}, clear=True)
    def test_disabled_without_base_url(self, ensure_workers):
        save_image_history_async(
            generation_id="generation-1",
            image=b"image",
            provider="ark",
            model="model",
            prompt="cat",
        )

        ensure_workers.assert_not_called()

    @patch("lib.image_generation_history._ensure_workers_started")
    @patch.dict(
        "os.environ",
        {"IMAGE_HISTORY_API_BASE_URL": "https://data.example"},
        clear=True,
    )
    def test_missing_api_key_logs_and_does_not_start_worker(self, ensure_workers):
        with self.assertLogs("lib.image_generation_history", level="ERROR"):
            save_image_history_async(
                generation_id="generation-1",
                image=b"image",
                provider="ark",
                model="model",
                prompt="cat",
            )

        ensure_workers.assert_not_called()

    @patch("lib.image_generation_history._ensure_workers_started")
    @patch.dict(
        "os.environ",
        {
            "IMAGE_HISTORY_API_BASE_URL": "https://data.example",
            "IMAGE_HISTORY_API_KEY": "secret",
        },
        clear=True,
    )
    def test_oversized_image_is_skipped_before_starting_workers(self, ensure_workers):
        with self.assertLogs("lib.image_generation_history", level="ERROR"):
            save_image_history_async(
                generation_id="generation-1",
                image=b"x" * (25 * 1024 * 1024 + 1),
                provider="ark",
                model="model",
                prompt="cat",
            )

        ensure_workers.assert_not_called()

    @patch("lib.image_generation_history._history_queue")
    @patch("lib.image_generation_history._ensure_workers_started", return_value=True)
    @patch.dict(
        "os.environ",
        {
            "IMAGE_HISTORY_API_BASE_URL": "https://data.example/base/",
            "IMAGE_HISTORY_API_KEY": "secret",
        },
        clear=True,
    )
    def test_enqueues_raw_image_without_encoding_in_caller(self, _ensure, history_queue):
        save_image_history_async(
            generation_id="generation-1",
            image=b"image",
            provider="ark",
            model="model",
            prompt="cat",
            size="2K",
        )

        job = history_queue.put_nowait.call_args.args[0]
        self.assertEqual(job.base_url, "https://data.example/base/")
        self.assertEqual(job.api_key, "secret")
        self.assertEqual(job.image, b"image")
        self.assertEqual(job.size, "2K")

    @patch("lib.image_generation_history._ensure_workers_started", return_value=False)
    @patch.dict(
        "os.environ",
        {
            "IMAGE_HISTORY_API_BASE_URL": "https://data.example",
            "IMAGE_HISTORY_API_KEY": "secret",
        },
        clear=True,
    )
    def test_worker_start_failure_is_logged_and_suppressed(self, _ensure):
        with self.assertLogs("lib.image_generation_history", level="ERROR"):
            save_image_history_async(
                generation_id="generation-1",
                image=b"image",
                provider="ark",
                model="model",
                prompt="cat",
            )

    @patch("lib.image_generation_history._history_queue")
    @patch("lib.image_generation_history._ensure_workers_started", return_value=True)
    @patch.dict(
        "os.environ",
        {
            "IMAGE_HISTORY_API_BASE_URL": "https://data.example",
            "IMAGE_HISTORY_API_KEY": "secret",
        },
        clear=True,
    )
    def test_full_queue_is_logged_and_suppressed(self, _ensure, history_queue):
        import queue

        history_queue.put_nowait.side_effect = queue.Full
        with self.assertLogs("lib.image_generation_history", level="ERROR"):
            save_image_history_async(
                generation_id="generation-1",
                image=b"image",
                provider="ark",
                model="model",
                prompt="cat",
            )

    @patch("lib.image_generation_history.time.sleep")
    @patch("lib.image_generation_history.requests.post")
    def test_retries_three_total_attempts_then_logs(self, post, _sleep):
        post.side_effect = requests.ConnectionError("offline")

        with self.assertLogs("lib.image_generation_history", level="ERROR") as logs:
            _save_image_history(
                base_url="https://data.example",
                api_key="secret",
                generation_id="generation-1",
                payload={"imageBase64": "aW1hZ2U=", "model": "model", "prompt": "cat"},
            )

        self.assertEqual(post.call_count, MAX_ATTEMPTS)
        self.assertIn("generation-1", logs.output[0])
        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://data.example/api/v1/image-generation/history",
        )
        self.assertEqual(
            request.kwargs["headers"]["Idempotency-Key"],
            "image-import:generation-1",
        )
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer secret")

    @patch("lib.image_generation_history.time.sleep")
    @patch("lib.image_generation_history.requests.post")
    def test_permanent_client_error_is_not_retried(self, post, sleep):
        response = Mock(status_code=401)
        post.side_effect = requests.HTTPError("unauthorized", response=response)

        with self.assertLogs("lib.image_generation_history", level="ERROR"):
            _save_image_history(
                base_url="https://data.example",
                api_key="bad-key",
                generation_id="generation-1",
                payload={"imageBase64": "aW1hZ2U=", "model": "model", "prompt": "cat"},
            )

        post.assert_called_once()
        sleep.assert_not_called()

    @patch("lib.image_generation_history.requests.post")
    def test_success_is_not_retried(self, post):
        post.return_value = Mock()

        _save_image_history(
            base_url="https://data.example",
            api_key="secret",
            generation_id="generation-1",
            payload={"imageBase64": "aW1hZ2U=", "model": "model", "prompt": "cat"},
        )

        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
