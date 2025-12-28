
import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import json
import base64
import time

# Mock environment variables before importing server
with patch.dict(os.environ, {"VOLC_APPID": "test_app_id", "VOLC_ACCESS_TOKEN": "test_token", "REDIS_URL": "redis://mock"}):
    # Mock redis before importing server
    with patch("redis.from_url") as mock_redis_init:
        mock_redis = MagicMock()
        mock_redis_init.return_value = mock_redis
        from server import app, process_podcast_task

class PodcastAsyncValidationTest(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        # Reset redis mock
        from server import redis_client
        self.redis_client = redis_client
        self.redis_client.reset_mock()

    @patch("server.threading.Thread")
    def test_podcast_endpoint_async_submit(self, MockThread):
        payload = {
            "scripts": [{"speaker": "s1", "text": "t1"}],
            "use_head_music": True
        }
        
        response = self.app.post("/v1/voice/podcast", 
                                 data=json.dumps(payload),
                                 content_type="application/json")
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("task_id", data)
        task_id = data["task_id"]
        
        # Verify initial redis state set
        self.redis_client.setex.assert_called_once()
        args, _ = self.redis_client.setex.call_args
        self.assertEqual(args[0], f"podcast_task:{task_id}")
        # TTL check
        self.assertEqual(args[1], 7 * 24 * 3600)
        stored_data = json.loads(args[2])
        self.assertEqual(stored_data["status"], "processing")
        self.assertEqual(stored_data["task_id"], task_id)
        
        # Verify thread started
        MockThread.assert_called_once()
        thread_args = MockThread.call_args[1]
        self.assertEqual(thread_args["target"], process_podcast_task)
        self.assertEqual(thread_args["args"][0], task_id)
        self.assertEqual(thread_args["args"][1], payload["scripts"])
        self.assertEqual(thread_args["args"][2], True) # head music
        
        MockThread.return_value.start.assert_called_once()

    def test_query_podcast_task_found(self):
        task_id = "some-uuid"
        mock_data = {
            "status": "success",
            "voice_b64": "fake_b64",
            "task_id": task_id
        }
        self.redis_client.get.return_value = json.dumps(mock_data).encode("utf-8")
        
        response = self.app.get(f"/v1/voice/podcast/{task_id}")
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), mock_data)
        self.redis_client.get.assert_called_with(f"podcast_task:{task_id}")

    def test_query_podcast_task_not_found(self):
        self.redis_client.get.return_value = None
        response = self.app.get("/v1/voice/podcast/missing-id")
        self.assertEqual(response.status_code, 404)

    @patch("server.PodcastTTSClient")
    def test_process_podcast_task_success(self, MockClient):
        mock_client_instance = MockClient.return_value
        async def async_mock(*args, **kwargs):
            return b"audio_bytes"
        mock_client_instance.generate_audio.side_effect = async_mock
        
        task_id = "task-123"
        scripts = [{"text": "hi"}]
        
        # Run the background function directly
        process_podcast_task(task_id, scripts, False, False)
        
        # Verify redis update for success
        self.redis_client.setex.assert_called()
        call_args = self.redis_client.setex.call_args
        key = call_args[0][0]
        ttl = call_args[0][1]
        val = json.loads(call_args[0][2])
        
        self.assertEqual(key, f"podcast_task:{task_id}")
        self.assertEqual(val["status"], "success")
        self.assertIn("voice_b64", val)
        # Expected base64 of "audio_bytes" is "YXVkaW9fYnl0ZXM="
        self.assertEqual(val["voice_b64"], "YXVkaW9fYnl0ZXM=")

    @patch("server.PodcastTTSClient")
    def test_process_podcast_task_failure(self, MockClient):
        mock_client_instance = MockClient.return_value
        async def async_mock(*args, **kwargs):
            raise RuntimeError("TTS Error")
        mock_client_instance.generate_audio.side_effect = async_mock
        
        task_id = "task-err"
        
        process_podcast_task(task_id, [], False, False)
        
        self.redis_client.setex.assert_called()
        val = json.loads(self.redis_client.setex.call_args[0][2])
        
        self.assertEqual(val["status"], "failed")
        self.assertEqual(val["error"], "TTS Error")

if __name__ == "__main__":
    unittest.main()
