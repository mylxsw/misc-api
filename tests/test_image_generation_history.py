import unittest
from unittest.mock import Mock, patch

import requests

from lib.image_generation_history import (
    MAX_ATTEMPTS,
    _save_image_history,
    save_image_history_async,
)


class ImageGenerationHistoryTests(unittest.TestCase):
    @patch("lib.image_generation_history.threading.Thread")
    @patch.dict("os.environ", {}, clear=True)
    def test_disabled_without_base_url(self, thread_class):
        save_image_history_async(
            generation_id="generation-1",
            image=b"image",
            provider="ark",
            model="model",
            prompt="cat",
        )

        thread_class.assert_not_called()

    @patch("lib.image_generation_history.threading.Thread")
    @patch.dict(
        "os.environ",
        {
            "IMAGE_HISTORY_API_BASE_URL": "https://data.example/base/",
            "IMAGE_HISTORY_API_KEY": "secret",
        },
        clear=True,
    )
    def test_schedules_daemon_worker_with_payload(self, thread_class):
        save_image_history_async(
            generation_id="generation-1",
            image=b"image",
            provider="ark",
            model="model",
            prompt="cat",
            size="2K",
        )

        kwargs = thread_class.call_args.kwargs
        self.assertTrue(kwargs["daemon"])
        worker_kwargs = kwargs["kwargs"]
        self.assertEqual(worker_kwargs["base_url"], "https://data.example/base/")
        self.assertEqual(worker_kwargs["api_key"], "secret")
        self.assertEqual(worker_kwargs["payload"]["imageBase64"], "aW1hZ2U=")
        self.assertEqual(worker_kwargs["payload"]["size"], "2K")
        thread_class.return_value.start.assert_called_once_with()

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
