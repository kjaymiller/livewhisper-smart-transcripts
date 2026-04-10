#!/bin/bash

# start.sh
# Starts the entire stack: PostgreSQL Database, Web App (FastAPI) via Docker Compose,
# and WhisperLiveKit Server locally on the Mac (using MLX).

echo "====================================="
echo " Starting Transcription App Stack    "
echo "====================================="

WLK_PORT=${WLK_PORT:-9090}

# 1. Start WhisperLiveKit Server in the background locally (for MLX hardware acceleration)
# Check if wlk is already running on the configured port
if lsof -Pi :$WLK_PORT -sTCP:LISTEN -t >/dev/null; then
	echo "[1/2] WhisperLiveKit is already running on port $WLK_PORT."
else
	echo "[1/2] Starting WhisperLiveKit (MLX Backend) on port $WLK_PORT..."
	# We use pipx or global uv to ensure wlk is available, or install it if missing
	uv tool install whisperlivekit --with python-multipart --with mlx-whisper --with "git+https://github.com/NVIDIA/NeMo.git@main#egg=nemo_toolkit[asr]" 2>/dev/null || true

	uv run wlk --host 0.0.0.0 --port $WLK_PORT --backend mlx-whisper --model base --language en --diarization --pcm-input >wlk.log 2>&1 &
	WLK_PID=$!
	echo "WhisperLiveKit started with PID $WLK_PID. Logging to wlk.log."

	# Wait a moment for WLK to initialize
	sleep 3
fi

APP_PORT=${APP_PORT:-8000}

# 2. Start PostgreSQL and FastAPI Web App via Docker Compose
echo "[2/2] Starting Database and Web Application via Docker Compose..."
echo "Building and starting containers... (Web App will be at http://localhost:${APP_PORT})"
APP_PORT=${APP_PORT} docker compose up --build -d
