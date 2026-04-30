# conduit-transcripts justfile

set shell := ["bash", "-c"]

db_url := "$(op read 'op://Private/Aiven - Conduit Transcriptions/Connection String')"
hf_access_token := "$(op://Private/Conduit Transcription/hf_access_token')"

# Default recipe - show available commands
@default:
    just --list

# ==========================================
# Stack Management (Start / Stop)
# ==========================================

# Start the entire application stack (WhisperLiveKit & Docker containers) in daemon mode
start:
    ./start.sh

# Alias for start
up: start

# Stop the entire application stack gracefully
stop:
    ./shutdown.sh

# Alias for stop
down: stop

# View logs for Docker containers
logs:
    docker compose logs -f

# View logs for the WhisperLiveKit server
logs-whisper:
    tail -f wlk.log

# Access the PostgreSQL database shell directly
db-shell:
    psql "{{db_url}}"

# ==========================================
# Application / Transcription Commands
# ==========================================

# Transcribe one or more audio files (e.g. `just transcribe audio1.wav`)
transcribe +FILES:
    DATABASE_URL="{{db_url}}" uv run --frozen app/cli.py transcribe {{FILES}}

# Show CLI transcription help
help-transcribe:
    uv run transcribe --help

# List all active, in-progress transcriptions
active:
    DATABASE_URL="{{db_url}}" uv run --frozen app/cli.py active

# Clear all stuck or orphaned in-progress transcriptions from the database and Valkey
clear-active:
    @echo "Clearing stuck transcription keys from Valkey..."
    docker exec conduit-valkey valkey-cli keys "transcription_progress:*" | xargs -I {} docker exec conduit-valkey valkey-cli del "{}" || true
    @echo "Deleting orphaned processing records from PostgreSQL..."
    psql "{{db_url}}" -c "DELETE FROM transcription WHERE status = 'processing';"
    @echo "Done."

# ==========================================
# UV / Dependency Management
# ==========================================

# Sync and install dependencies
sync:
    uv sync --extra transcription

# Update lockfile dependencies
lock:
    uv lock

# Upgrade dependencies and recreate lockfile
upgrade-deps:
    uv lock --upgrade
    uv sync --extra transcription

# ==========================================
# Code Quality & Cleanup
# ==========================================

# Run Python formatting with Ruff
fmt:
    uv tool run ruff format .

# Run Python linting with Ruff
lint:
    uv tool run ruff check .

# Fix Python linting with Ruff
lint-fix:
    uv tool run ruff check . --fix

# Clean up Python cache and build artifacts
@clean:
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete
    find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
    echo "✓ Cleaned up cache and build artifacts"

# ==========================================
# Issue Tracking (bd/beads)
# ==========================================

# List all open issues
@bd-list:
    bd list

# Show ready work (unblocked issues)
@bd-ready:
    bd ready

# Show issue details (e.g., just bd-show bd-1)
@bd-show ISSUE_ID:
    bd show {{ISSUE_ID}}
