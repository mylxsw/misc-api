
import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import json
import base64
import time

# Mock environment variables before importing server
with patch.dict(os.environ, {"VOLC_APPID": "test_app_id", "VOLC_ACCESS_TOKEN": "test_token", "REDIS_URL": "redis://mock", "DASHSCOPE_API_KEY": "mock_key"}):
    # Mock redis before importing server
    with patch("redis.from_url") as mock_redis_init:
        mock_redis = MagicMock()
        mock_redis_init.return_value = mock_redis
        from server import app, process_cosyvoice_task

class CosyVoiceAsyncValidationTest(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        # Reset redis mock
        from server import redis_client
        self.redis_client = redis_client
        self.redis_client.reset_mock()

    @patch("server.threading.Thread")
    def test_cosyvoice_endpoint_async_submit(self, MockThread):
        payload = {
            "text": "Hello world",
            "voice": "test_voice",
            "model": "test_model"
        }
        
        response = self.app.post("/v1/voice/cosyvoice/async", 
                                 data=json.dumps(payload),
                                 content_type="application/json")
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("task_id", data)
        task_id = data["task_id"]
        
        # Verify initial redis state set
        self.redis_client.setex.assert_called_once()
        args, _ = self.redis_client.setex.call_args
        self.assertEqual(args[0], f"cosyvoice_task:{task_id}")
        # TTL check (REDIS_TTL is 7 days)
        self.assertEqual(args[1], 7 * 24 * 3600)
        stored_data = json.loads(args[2])
        self.assertEqual(stored_data["status"], "processing")
        self.assertEqual(stored_data["task_id"], task_id)
        
        # Verify thread started
        MockThread.assert_called_once()
        thread_args = MockThread.call_args[1]
        self.assertEqual(thread_args["target"], process_cosyvoice_task)
        self.assertEqual(thread_args["args"][0], task_id)
        self.assertEqual(thread_args["args"][1], payload["text"])
        self.assertEqual(thread_args["args"][2], payload["voice"])
        self.assertEqual(thread_args["args"][3], payload["model"])
        
        MockThread.return_value.start.assert_called_once()

    def test_query_cosyvoice_task_found(self):
        task_id = "some-uuid"
        mock_data = {
            "status": "success",
            "voice_b64": "fake_b64",
            "task_id": task_id
        }
        self.redis_client.get.return_value = json.dumps(mock_data).encode("utf-8")
        
        response = self.app.get(f"/v1/voice/cosyvoice/async/{task_id}")
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), mock_data)
        self.redis_client.get.assert_called_with(f"cosyvoice_task:{task_id}")

    def test_query_cosyvoice_task_not_found(self):
        self.redis_client.get.return_value = None
        response = self.app.get("/v1/voice/cosyvoice/async/missing-id")
        self.assertEqual(response.status_code, 404)

    @patch("server.synthesize")
    def test_process_cosyvoice_task_success(self, mock_synthesize):
        # synthesize returns (audio_bytes, request_id, first_pkg_delay)
        mock_synthesize.return_value = (b"audio_bytes", "req-123", 100)
        
        task_id = "task-123"
        text = "Hello"
        voice = "v1"
        model = "m1"
        kwargs = {}
        
        # Run the background function directly
        process_cosyvoice_task(task_id, text, voice, model, kwargs)
        
        # Verify redis update for success
        self.redis_client.setex.assert_called()
        call_args = self.redis_client.setex.call_args
        key = call_args[0][0]
        val = json.loads(call_args[0][2])
        
        self.assertEqual(key, f"cosyvoice_task:{task_id}")
        self.assertEqual(val["status"], "success")
        self.assertIn("voice_b64", val)
        # Expected base64 of "audio_bytes" is "YXVkaW9fYnl0ZXM="
        self.assertEqual(val["voice_b64"], "YXVkaW9fYnl0ZXM=")
        self.assertEqual(val["request_id"], "req-123")
        self.assertEqual(val["first_package_delay_ms"], 100)

    @patch("server.synthesize")
    def test_process_cosyvoice_task_failure(self, mock_synthesize):
        mock_synthesize.side_effect = RuntimeError("TTS Error")
        
        task_id = "task-err"
        
        process_cosyvoice_task(task_id, "text", "voice", "model", {})
        
        self.redis_client.setex.assert_called()
        val = json.loads(self.redis_client.setex.call_args[0][2])
        
        self.assertEqual(val["status"], "failed")
        self.assertEqual(val["error"], "TTS Error")

if __name__ == "__main__":
    unittest.main()
