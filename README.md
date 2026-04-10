# Conduit Podcast Transcripts

This is an archive of transcriptions for the [Conduit Podcast](https://relay.fm/conduit).

The application provides a minimal FastAPI web interface to search and view transcripts, and a CLI tool for transcribing new audio using an external WhisperLiveKit server.

## Getting Started

### Installation

This project uses `uv` for fast, reliable Python package management. To set up locally for development:

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies and create virtual environment
uv sync
```

### Docker Setup

The easiest way to run the application is using the provided `start.sh` script. This script will automatically start the `WhisperLiveKit` server locally in the background (using Apple Silicon MLX acceleration) and then launch the PostgreSQL database and FastAPI web app via Docker Compose.

```bash
# Make the script executable (if it isn't already)
chmod +x start.sh

# Start the entire stack
./start.sh
```

The web interface will be available at `http://localhost:8000` (or whichever port is defined via the `APP_PORT` environment variable).

**Note:** If you want to run things manually, you can start the Docker containers with `docker compose up --build -d` but you will need to manage the WhisperLiveKit server separately.

## Usage

### Transcription (CLI)

The CLI tool allows you to send audio files to the WhisperLiveKit server and save the transcriptions to the database.

**Prerequisite:** Ensure the stack is running via `./start.sh` so that WhisperLiveKit is available.

By default, it looks for WhisperLiveKit at `http://localhost:9090/v1/audio/transcriptions`.

If you're running locally with `uv`:
```bash
# Transcribe a single audio file
uv run transcribe path/to/audio_file.wav

# Pass a custom WhisperLiveKit URL
WLK_URL="http://your-whisper-server:9090/v1/audio/transcriptions" uv run transcribe audio.wav

# You can pass multiple files at once:
uv run transcribe audio1.wav audio2.wav
```

### Web Interface

Once the containers are running, navigate to `http://localhost:8000` (or the custom `APP_PORT`) to view and correct transcripts. 
The API endpoints are available at:
- `GET /api/transcriptions` - List all transcriptions
- `PUT /api/transcriptions/{record_id}` - Update a transcription

## Project Structure

- `app/` - Core application logic
  - `main.py` - FastAPI web application
  - `cli.py` - Command-line interface for transcriptions
  - `database.py` - Database models and connection logic
  - `static/` - HTML/JS assets for the web interface
- `docker-compose.yml` - Docker Compose configuration
- `Dockerfile` - Docker image definition

## Technology Stack

- Python 3.13+
- FastAPI
- SQLModel (SQLAlchemy)
- PostgreSQL
- Click (CLI framework)
- httpx (Async HTTP client)

## Usage and License

<p xmlns:cc="http://creativecommons.org/ns#" xmlns:dct="http://purl.org/dc/terms/"><a property="dct:title" rel="cc:attributionURL" href="https://github.com/kjaymiller/conduit-transcripts">Conduit Podcast Transcripts</a> by <a rel="cc:attributionURL dct:creator" property="cc:attributionName" href="https://relay.fm/conduit">Jay Miller, Kathy Campbell, original downloads from whisper work done by Pilix</a> is licensed under <a href="http://creativecommons.org/licenses/by-nc-sa/4.0/?ref=chooser-v1" target="_blank" rel="license noopener noreferrer" style="display:inline-block;">Attribution-NonCommercial-ShareAlike 4.0 International<img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/cc.svg?ref=chooser-v1"><img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/by.svg?ref=chooser-v1"><img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/nc.svg?ref=chooser-v1"><img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/sa.svg?ref=chooser-v1"></a></p>
