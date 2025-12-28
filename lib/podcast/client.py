
import asyncio
import json
import logging
import os
import time
import uuid
import websockets
from typing import List, Dict, Optional, Any

from .protocols import (
    EventType,
    MsgType,
    finish_connection,
    finish_session,
    receive_message,
    start_connection,
    start_session,
    wait_for_event,
)

logger = logging.getLogger("PodcastTTS")

ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sami/podcasttts"
DEFAULT_RESOURCE_ID = "volc.service_type.10050"


class PodcastTTSClient:
    def __init__(self, appid: str, access_token: str, cluster: str = DEFAULT_RESOURCE_ID):
        self.appid = appid
        self.access_token = access_token
        self.cluster = cluster

    async def generate_audio(self, scripts: List[Dict[str, str]], 
                             action: int = 3, 
                             encoding: str = "mp3",
                             request_id: Optional[str] = None,
                             use_head_music: bool = False,
                             use_tail_music: bool = False) -> bytes:
        """
        Generate podcast audio from scripts.
        
        Args:
            scripts: List of dicts with 'speaker' and 'text' keys.
            action: 3 for NLP texts (default based on requirement).
            encoding: Audio format (mp3 or wav).
            request_id: Unique identifier for the request.
            
        Returns:
            bytes: The generated audio data.
        """
        if not request_id:
            request_id = str(uuid.uuid4())
            
        headers = {
            "X-Api-App-Id": self.appid,
            "X-Api-App-Key": "aGjiRDfUWi",
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.cluster,
            "X-Api-Connect-Id": request_id,
        }

        # Request parameters
        req_params = {
            "input_id": request_id,
            "nlp_texts": scripts,
            "action": action,
            "use_head_music": use_head_music,
            "use_tail_music": use_tail_music,
            "input_info": {
                "return_audio_url": False,
                "only_nlp_text": False,
            },
            "audio_config": {
                "format": encoding,
                "sample_rate": 24000,
                "speech_rate": 0
            }
        }

        podcast_audio = bytearray()
        audio = bytearray()
        
        is_podcast_round_end = True
        audio_received = False
        last_round_id = -1
        task_id = ""
        retry_num = 3
        
        while retry_num > 0:
            websocket = None
            try:
                websocket = await websockets.connect(ENDPOINT, additional_headers=headers)
                
                if not is_podcast_round_end:
                     req_params["retry_info"] = {
                        "retry_task_id": task_id,
                        "last_finished_round_id": last_round_id
                    }

                # Start connection
                await start_connection(websocket)
                await wait_for_event(websocket, MsgType.FullServerResponse, EventType.ConnectionStarted)

                session_id = str(uuid.uuid4())
                if not task_id:
                    task_id = session_id
                
                # Start session
                await start_session(websocket, json.dumps(req_params).encode(), session_id)
                await wait_for_event(websocket, MsgType.FullServerResponse, EventType.SessionStarted)
                
                # Finish session (trigger processing)
                await finish_session(websocket, session_id)

                while True:
                    msg = await receive_message(websocket)

                    if msg.type == MsgType.AudioOnlyServer and msg.event == EventType.PodcastRoundResponse:
                        if not audio_received and audio:
                            audio_received = True
                        audio.extend(msg.payload)
                    
                    elif msg.type == MsgType.Error:
                        raise RuntimeError(f"Server error: {msg.payload.decode()}")
                    
                    elif msg.type == MsgType.FullServerResponse:
                        if msg.event == EventType.PodcastRoundStart:
                            data = json.loads(msg.payload.decode("utf-8"))
                            current_round = data.get("round_id")
                            is_podcast_round_end = False
                            logger.info(f"New round started: {data}")
                        
                        if msg.event == EventType.PodcastRoundEnd:
                            data = json.loads(msg.payload.decode("utf-8"))
                            if data.get("is_error"):
                                break
                            is_podcast_round_end = True
                            last_round_id = current_round if 'current_round' in locals() else -1
                            
                            podcast_audio.extend(audio)
                            audio.clear()
                            
                    if msg.event == EventType.SessionFinished:
                        break
                
                if not audio_received and not podcast_audio:
                     # If we finished but got no audio, check if we have accumulated podcast_audio
                     # Logic check: audio_received flag seems to track if we got *any* chunk in current loop?
                     # The original code logic for audio_received seems a bit weird: "if not audio_received and audio: audio_received = True" inside the loop. 
                     # But basically if we have podcast_audio, we are good.
                     pass

                # Clean close
                await finish_connection(websocket)
                await wait_for_event(websocket, MsgType.FullServerResponse, EventType.ConnectionFinished)
                
                if is_podcast_round_end:
                    return bytes(podcast_audio)
                else:
                    logger.warning(f"Podcast not finished, retrying. Last round: {last_round_id}")
                    retry_num -= 1
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in podcast generation: {e}")
                retry_num -= 1
                if retry_num <= 0:
                    raise
                await asyncio.sleep(1)
            finally:
                if websocket:
                    await websocket.close()
        
        raise RuntimeError("Failed to generate podcast audio after retries")
