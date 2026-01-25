# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Flask-based HTTP API service integrating multiple AI media generation capabilities:
- Alibaba Cloud DashScope CosyVoice TTS
- Volcano Engine Podcast TTS (multi-speaker)
- Fish Audio TTS
- Image stitching utility

## Commands

```bash
# Install dependencies
uv sync --no-dev

# Run server (listens on localhost:8000)
uv run python server.py

# Docker build and run
docker build -t cosyvoice-api .
docker run -p 8000:8000 -e DASHSCOPE_API_KEY=your_key cosyvoice-api

# Run tests
python verify_cosyvoice_async.py
python verify_fish_audio.py
python verify_podcast.py
./verify_large.sh
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DASHSCOPE_API_KEY` | Yes | Alibaba Cloud DashScope API Key |
| `VOLC_APPID` | For Podcast | Volcano Engine App ID |
| `VOLC_ACCESS_TOKEN` | For Podcast | Volcano Engine Access Token |
| `FISH_API_KEY` | For Fish Audio | Fish Audio API Key |
| `REDIS_URL` | No | Redis URL (default: `redis://localhost:6379/0`) |
| `PORT` | No | Server port (default: 8000) |

## Architecture

**Main entry point:** `server.py` - Flask app with `create_app()` factory for Gunicorn

**Async task pattern:** All long-running TTS operations use background threads with Redis for state management. Endpoints return `task_id` for polling. Task results have 7-day TTL.

**lib/podcast/**: Volcano Engine Podcast TTS WebSocket client
- `client.py` - Main client with retry logic (3 attempts)
- `protocols.py` - WebSocket protocol handlers

**Response format:** Audio data returned as base64-encoded strings in JSON responses.
