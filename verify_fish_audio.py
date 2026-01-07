
import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import json
import base64
import time
import sys

# Mock dependencies globally BEFORE importing server
mock_fishaudio = MagicMock()
mock_fishaudio_types = MagicMock()
mock_pydub = MagicMock()

sys.modules["fishaudio"] = mock_fishaudio
sys.modules["fishaudio.types"] = mock_fishaudio_types
sys.modules["pydub"] = mock_pydub

# Setup attributes on mocks to avoid AttributeError during server import/usage
mock_fishaudio.FishAudio = MagicMock()
mock_fishaudio_types.TTSConfig = MagicMock()
mock_fishaudio_types.Prosody = MagicMock()
mock_pydub.AudioSegment = MagicMock()

# Mock environment variables before importing server
# We can't use patch.dict only for import if we want it global, 
# but for os.environ we usually want it. 
# However, modifying os.environ globally is fine for this script.
os.environ["VOLC_APPID"] = "test_app_id"
os.environ["VOLC_ACCESS_TOKEN"] = "test_token"
os.environ["REDIS_URL"] = "redis://mock"
os.environ["DASHSCOPE_API_KEY"] = "mock_key"
os.environ["FISH_API_KEY"] = "test_fish_key"

# Mock redis global import
mock_redis = MagicMock()
sys.modules["redis"] = mock_redis
mock_redis.from_url.return_value = MagicMock()

# Now import server
from server import app, process_fish_audio_task, redis_client

class FishAudioValidationTest(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        self.redis_client = redis_client
        self.redis_client.reset_mock()

    @patch("server.threading.Thread")
    def test_fish_audio_endpoint_submit(self, MockThread):
        payload = {
            "text": "Hello Fish Audio",
            "reference_id": "voice_123",
            "speed": 1.2
        }
        
        response = self.app.post("/v1/voice/fish-audio/text-to-speech", 
                                 data=json.dumps(payload),
                                 content_type="application/json")
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("task_id", data)
        task_id = data["task_id"]
        
        # Verify initial redis state set
        self.redis_client.setex.assert_called_once()
        args, _ = self.redis_client.setex.call_args
        self.assertEqual(args[0], f"fishaudio_task:{task_id}")
        stored_data = json.loads(args[2])
        self.assertEqual(stored_data["status"], "processing")
        self.assertEqual(stored_data["task_id"], task_id)
        
        # Verify thread started
        MockThread.assert_called_once()
        thread_args = MockThread.call_args[1]
        self.assertEqual(thread_args["target"], process_fish_audio_task)
        self.assertEqual(thread_args["args"][0], task_id)
        self.assertEqual(thread_args["args"][1], payload["text"])
        self.assertEqual(thread_args["args"][2], payload["reference_id"])
        # speed is at index 4
        self.assertEqual(thread_args["args"][4], payload["speed"])
        
        MockThread.return_value.start.assert_called_once()

    def test_query_fish_audio_task_found(self):
        task_id = "fish-uuid"
        mock_data = {
            "status": "success",
            "voice_b64": "fishy_b64",
            "task_id": task_id
        }
        self.redis_client.get.return_value = json.dumps(mock_data).encode("utf-8")
        
        response = self.app.get(f"/v1/voice/fish-audio/text-to-speech/{task_id}")
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data), mock_data)
        self.redis_client.get.assert_called_with(f"fishaudio_task:{task_id}")

    def test_process_fish_audio_task_success(self):
        # We need to mock FishAudio class usage inside the module
        # Since we mocked sys.modules["fishaudio"].FishAudio, that's what server.py uses.
        
        mock_client = mock_fishaudio.FishAudio.return_value
        # tts.convert returns a generator (bytes)
        mock_client.tts.convert.return_value = iter([b"chunk1", b"chunk2"])
        
        task_id = "task-fish"
        text = "Hello"
        
        # Run the background function directly
        process_fish_audio_task(task_id, text, reference_id="ref_1")
        
        # Verify FishAudio client init
        mock_fishaudio.FishAudio.assert_called_with(api_key="test_fish_key")
        
        # Verify redis update for success
        self.redis_client.setex.assert_called()
        call_args = self.redis_client.setex.call_args
        key = call_args[0][0]
        val = json.loads(call_args[0][2])
        
        self.assertEqual(key, f"fishaudio_task:{task_id}")
        self.assertEqual(val["status"], "success")
        self.assertIn("voice_b64", val)
        # Expected base64 of b"chunk1chunk2"
        expected_b64 = base64.b64encode(b"chunk1chunk2").decode("ascii")
        self.assertEqual(val["voice_b64"], expected_b64)

    def test_process_fish_audio_task_failure(self):
        mock_fishaudio.FishAudio.side_effect = RuntimeError("Fish Error")
        
        task_id = "task-err"
        
        process_fish_audio_task(task_id, "text")
        
        self.redis_client.setex.assert_called()
        val = json.loads(self.redis_client.setex.call_args[0][2])
        
        self.assertEqual(val["status"], "failed")
        self.assertEqual(val["error"], "Fish Error")
        
        # Reset side effect for other tests if any
        mock_fishaudio.FishAudio.side_effect = None

if __name__ == "__main__":
    unittest.main()
