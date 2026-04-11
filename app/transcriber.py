import os
import json
import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from sqlmodel import Session, select
import valkey
import websockets

from app.database import engine, Transcription, Diff

logger = logging.getLogger(__name__)

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
WLK_WS_URL = os.environ.get("WLK_WS_URL", "ws://localhost:9090/asr")

# Setup Valkey client
vk = valkey.from_url(VALKEY_URL, decode_responses=True)

# ------------------------------------------------------------------------
# Standalone Transcription Client (Adapted from whisperlivekit)
# We embed this here so we have full control over the websocket connection
# keepalive intervals (ping_interval=None) to prevent timeouts on long files.
# ------------------------------------------------------------------------

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # s16le


@dataclass
class TranscriptionResult:
    responses: List[dict] = field(default_factory=list)
    audio_duration: float = 0.0

    @property
    def lines(self) -> List[dict]:
        for resp in reversed(self.responses):
            if resp.get("lines"):
                return resp["lines"]
        return []


def load_audio_pcm(audio_path: str, sample_rate: int = SAMPLE_RATE) -> bytes:
    cmd = [
        "ffmpeg",
        "-i",
        str(audio_path),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-loglevel",
        "error",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr.decode().strip()}")
    if not proc.stdout:
        raise RuntimeError(f"ffmpeg produced no output for {audio_path}")
    return proc.stdout


async def robust_transcribe_audio(
    audio_path: str,
    url: str,
    chunk_duration: float = 0.5,
    speed: float = 0,
    timeout: float = 3600.0,
    on_response: Optional[callable] = None,
) -> TranscriptionResult:
    result = TranscriptionResult()
    pcm_data = load_audio_pcm(audio_path)
    result.audio_duration = len(pcm_data) / (SAMPLE_RATE * BYTES_PER_SAMPLE)
    chunk_bytes = int(chunk_duration * SAMPLE_RATE * BYTES_PER_SAMPLE)

    # Connect to WebSocket WITH KEEPALIVES DISABLED so long MLX inferences don't timeout
    async with websockets.connect(
        url, ping_interval=None, ping_timeout=None, close_timeout=None
    ) as ws:
        config_raw = await ws.recv()
        config_msg = json.loads(config_raw)
        is_pcm = config_msg.get("useAudioWorklet", False)

        done_event = asyncio.Event()

        async def send_audio():
            if is_pcm:
                offset = 0
                while offset < len(pcm_data):
                    end = min(offset + chunk_bytes, len(pcm_data))
                    await ws.send(pcm_data[offset:end])
                    offset = end
                    if speed > 0:
                        await asyncio.sleep(chunk_duration / speed)
            else:
                file_bytes = Path(audio_path).read_bytes()
                raw_chunk_size = 32000
                offset = 0
                while offset < len(file_bytes):
                    end = min(offset + raw_chunk_size, len(file_bytes))
                    await ws.send(file_bytes[offset:end])
                    offset = end
                    if speed > 0:
                        await asyncio.sleep(0.5 / speed)

            await ws.send(b"")  # EOF

        async def receive_results():
            try:
                async for raw_msg in ws:
                    data = json.loads(raw_msg)
                    if data.get("type") == "ready_to_stop":
                        done_event.set()
                        return

                    result.responses.append(data)
                    if on_response:
                        on_response(data)
            except Exception as e:
                logger.debug(f"Receiver ended: {e}")
            done_event.set()

        send_task = asyncio.create_task(send_audio())
        recv_task = asyncio.create_task(receive_results())

        total_timeout = (result.audio_duration / speed if speed > 0 else 1.0) + timeout

        try:
            await asyncio.wait_for(
                asyncio.gather(send_task, recv_task), timeout=total_timeout
            )
        except asyncio.TimeoutError:
            send_task.cancel()
            recv_task.cancel()
            try:
                await asyncio.gather(send_task, recv_task, return_exceptions=True)
            except:
                pass

    return result


# ------------------------------------------------------------------------
# End of standalone client
# ------------------------------------------------------------------------


def build_prompt_from_corrections(session: Session) -> str:
    diffs = session.exec(select(Diff).order_by(Diff.created_at.desc()).limit(20)).all()
    correction_texts = []
    for d in diffs:
        if d.original_phrase and d.corrected_phrase:
            correction_texts.append(f"{d.original_phrase} -> {d.corrected_phrase}")

    if not correction_texts:
        return ""
    return (
        "Make sure to use correct terminology based on previous corrections: "
        + ", ".join(correction_texts)
    )


async def background_transcribe_task(
    record_id: int, file_path: Path, delete_file_after: bool = False
):
    """Background task to transcribe audio via WebSocket, tracking progress in Valkey."""
    try:
        with Session(engine) as session:
            prompt_text = build_prompt_from_corrections(session)

        # Initialize progress in Valkey (expires in 24 hours just in case)
        vk.setex(
            f"transcription_progress:{record_id}",
            86400,
            json.dumps({"current": 0, "total": 1, "text": ""}),
        )

        def on_response_callback(data):
            lines = data.get("lines", [])
            buf = data.get("buffer_transcription", "")

            text_parts = []
            for line in lines:
                text = line.get("text", "").strip()
                speaker = line.get("speaker")
                if speaker is not None and speaker != -2:
                    text_parts.append(f"[Speaker {speaker}]: {text}")
                else:
                    text_parts.append(text)

            if buf:
                text_parts.append(f"(... {buf})")

            current_text = "\n\n".join(text_parts).strip()

            # Update progress
            vk.setex(
                f"transcription_progress:{record_id}",
                86400,
                json.dumps({"current": 1, "total": 1, "text": current_text}),
            )

        # Add prompt language via query parameter to bypass language detection
        url = WLK_WS_URL
        if prompt_text:
            import urllib.parse

            url += "?prompt=" + urllib.parse.quote(prompt_text)

        result = await robust_transcribe_audio(
            audio_path=str(file_path),
            url=url,
            chunk_duration=0.5,
            speed=20.0,  # Pace at 20x real-time so we don't overflow the ASGI websocket receive queue
            timeout=14400.0,  # Massive 4 hour timeout limit just in case
            on_response=on_response_callback,
        )

        # Build final text with speaker labels
        final_text_parts = []
        for line in result.lines:
            text = line.get("text", "").strip()
            speaker = line.get("speaker")
            if speaker is not None and speaker != -2:
                final_text_parts.append(f"[Speaker {speaker}]: {text}")
            else:
                final_text_parts.append(text)

        final_text = "\n\n".join(final_text_parts).strip()

        # Update final text in the Database
        with Session(engine) as session:
            record = session.get(Transcription, record_id)
            if record:
                if final_text:
                    record.original_text = final_text
                    record.status = "completed"
                    session.add(record)
                    session.commit()
                else:
                    # Failed or returned nothing, remove the record
                    session.delete(record)
                    session.commit()

    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"Background task error: {e}")
        with Session(engine) as session:
            record = session.get(Transcription, record_id)
            if record:
                session.delete(record)
                session.commit()
    finally:
        # Cleanup the originally uploaded file if requested
        if delete_file_after and file_path.exists():
            try:
                file_path.unlink()
            except:
                pass

        # Cleanup valkey progress
        vk.delete(f"transcription_progress:{record_id}")
