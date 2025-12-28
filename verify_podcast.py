
import unittest
from unittest.mock import patch, MagicMock
import os
import json
import base64

# Mock environment variables before importing server
with patch.dict(os.environ, {"VOLC_APPID": "test_app_id", "VOLC_ACCESS_TOKEN": "test_token"}):
    from server import app

class PodcastValidationTest(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    @patch("server.PodcastTTSClient")
    def test_podcast_endpoint_success(self, MockClient):
        # Setup mock
        mock_instance = MockClient.return_value
        # Mock generate_audio to return some bytes
        expected_audio = b"fake_podcast_audio"
        
        # Async mock setup
        async def async_mock(*args, **kwargs):
            return expected_audio
        
        mock_instance.generate_audio.side_effect = async_mock

        payload = {
            "scripts": [
                {"speaker": "speaker1", "text": "Hello world"}
            ]
        }
        
        response = self.app.post("/v1/voice/podcast", 
                                 data=json.dumps(payload),
                                 content_type="application/json")
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("voice_b64", data)
        
        decoded = base64.b64decode(data["voice_b64"])
        self.assertEqual(decoded, expected_audio)
        
        # Verify call arguments
        mock_instance.generate_audio.assert_called_once()
        args, kwargs = mock_instance.generate_audio.call_args
        self.assertEqual(args[0], payload["scripts"])
        # default should be False if not provided in payload, 
        # but in this test payload didn't provide them, server separates logic.
        # Check what server called.
        self.assertEqual(kwargs.get('use_head_music'), False)
        self.assertEqual(kwargs.get('use_tail_music'), False)

    @patch("server.PodcastTTSClient")
    def test_podcast_endpoint_with_music(self, MockClient):
        mock_instance = MockClient.return_value
        async def async_mock(*args, **kwargs):
            return b"music_audio"
        mock_instance.generate_audio.side_effect = async_mock

        payload = {
            "scripts": [{"speaker": "s1", "text": "t1"}],
            "use_head_music": True,
            "use_tail_music": True
        }
        
        response = self.app.post("/v1/voice/podcast", 
                                 data=json.dumps(payload),
                                 content_type="application/json")
        
        self.assertEqual(response.status_code, 200)
        
        mock_instance.generate_audio.assert_called_once()
        _, kwargs = mock_instance.generate_audio.call_args
        self.assertEqual(kwargs.get('use_head_music'), True)
        self.assertEqual(kwargs.get('use_tail_music'), True)

    def test_podcast_endpoint_missing_scripts(self):
        response = self.app.post("/v1/voice/podcast", 
                                 data=json.dumps({}),
                                 content_type="application/json")
        self.assertEqual(response.status_code, 400)

    @patch("server.PodcastTTSClient")
    def test_podcast_endpoint_error(self, MockClient):
        mock_instance = MockClient.return_value
        
        async def async_mock_error(*args, **kwargs):
            raise RuntimeError("TTS generation failed")
            
        mock_instance.generate_audio.side_effect = async_mock_error

        payload = {
            "scripts": [{"speaker": "s", "text": "t"}]
        }
        
        response = self.app.post("/v1/voice/podcast", 
                                 data=json.dumps(payload),
                                 content_type="application/json")
        
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertIn("error", data)
        self.assertIn("TTS generation failed", data["error"])

if __name__ == "__main__":
    unittest.main()
