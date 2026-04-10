#!/bin/bash

# shutdown.sh
# Gracefully shuts down the WhisperLiveKit server and Docker containers.

echo "====================================="
echo " Stopping Transcription App Stack    "
echo "====================================="

echo "[1/2] Stopping WhisperLiveKit (wlk) processes..."
WLK_PIDS=$(pgrep -f "wlk --host.*--port")

if [ -z "$WLK_PIDS" ]; then
	echo "  -> No WhisperLiveKit processes found running."
else
	pkill -f "wlk --host.*--port"
	sleep 2
	REMAINING=$(pgrep -f "wlk --host.*--port")
	if [ ! -z "$REMAINING" ]; then
		pkill -9 -f "wlk --host.*--port"
	fi
	echo "  -> WhisperLiveKit server has been shut down."
fi

echo "[2/2] Stopping Database and Web Application via Docker Compose..."
docker compose down

echo "✅ All services shut down successfully."
