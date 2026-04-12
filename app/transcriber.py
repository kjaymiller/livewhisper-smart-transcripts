import os
import json
import asyncio
import logging
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import List, Optional
from sqlmodel import Session, select
import valkey
import httpx

from app.database import engine, Transcription, Diff

logger = logging.getLogger(__name__)

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
WLK_URL = os.environ.get("WLK_URL", "http://localhost:9090/v1/audio/transcriptions")

# Setup Valkey client
vk = valkey.from_url(VALKEY_URL, decode_responses=True)


def get_audio_duration(file_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def split_audio(file_path: Path, chunk_duration: int = 600) -> list[Path]:
    """Splits an audio file into chunks of `chunk_duration` seconds."""
    duration = get_audio_duration(file_path)
    if duration <= chunk_duration:
        return [file_path]

    chunks = []
    temp_dir = Path(tempfile.mkdtemp(prefix="wlk_chunks_"))

    ext = file_path.suffix
    output_pattern = str(temp_dir / f"chunk_%03d{ext}")

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(file_path),
                "-f",
                "segment",
                "-segment_time",
                str(chunk_duration),
                "-c",
                "copy",
                output_pattern,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        chunks = sorted(list(temp_dir.glob(f"chunk_*{ext}")))
        return chunks
    except Exception as e:
        logger.error(f"Error splitting audio: {e}")
        return [file_path]


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


async def async_transcribe_chunk(
    client: httpx.AsyncClient, chunk_path: Path, prompt_text: str
) -> str:
    """Sends a single chunk to the WhisperLiveKit API."""
    try:
        with open(chunk_path, "rb") as f:
            data = {"model": "base", "response_format": "verbose_json"}
            if prompt_text:
                data["prompt"] = prompt_text

            # Set a massive timeout (1 hour per chunk) so the HTTP request never drops out
            response = await client.post(
                WLK_URL,
                files={"file": (chunk_path.name, f, "audio/wav")},
                data=data,
                timeout=3600.0,
            )

            if response.status_code == 200:
                result = response.json()
                segments = result.get("segments", [])

                # Format text with speaker labels if available
                text_parts = []
                for seg in segments:
                    text = seg.get("text", "").strip()
                    speaker = seg.get("speaker")

                    if speaker is not None and speaker != -2:
                        text_parts.append(f"[Speaker {speaker}]: {text}")
                    else:
                        text_parts.append(text)

                if not text_parts and result.get("text"):
                    return result.get("text", "")

                return "\n\n".join(text_parts)
            else:
                logger.error(f"API Error ({response.status_code}): {response.text}")
                return ""
    except Exception as e:
        logger.error(f"Error communicating with API: {e}")
        return ""


async def background_transcribe_task(
    record_id: int, file_path: Path, delete_file_after: bool = False
):
    try:
        with Session(engine) as session:
            prompt_text = build_prompt_from_corrections(session)

        # Chunk audio into 10 minute blocks to safely pass the stateless HTTP inference
        chunks = split_audio(file_path, chunk_duration=600)
        total_chunks = len(chunks)

        vk.setex(
            f"transcription_progress:{record_id}",
            86400,
            json.dumps({"current": 0, "total": total_chunks, "text": ""}),
        )

        full_transcription = []

        async with httpx.AsyncClient() as client:
            for idx, chunk in enumerate(chunks):
                text = await async_transcribe_chunk(client, chunk, prompt_text)
                if text:
                    full_transcription.append(text)

                current_text = "\n\n".join(full_transcription).strip()

                vk.setex(
                    f"transcription_progress:{record_id}",
                    86400,
                    json.dumps(
                        {
                            "current": idx + 1,
                            "total": total_chunks,
                            "text": current_text,
                        }
                    ),
                )

        final_text = "\n\n".join(full_transcription).strip()

        with Session(engine) as session:
            record = session.get(Transcription, record_id)
            if record:
                if final_text:
                    record.original_text = final_text
                    record.status = "completed"
                    session.add(record)
                    session.commit()
                else:
                    session.delete(record)
                    session.commit()

    except Exception as e:
        logger.error(f"Background task error: {e}")
        logger.debug(f"Traceback: {traceback.format_exc()}")
        with Session(engine) as session:
            record = session.get(Transcription, record_id)
            if record:
                session.delete(record)
                session.commit()
    finally:
        chunks_dir = (
            file_path.parent
            if file_path.parent.name.startswith("wlk_chunks_")
            else None
        )
        if (
            not chunks_dir
            and hasattr(chunks[0], "parent")
            and chunks[0].parent.name.startswith("wlk_chunks_")
        ):
            chunks_dir = chunks[0].parent

        if chunks_dir and chunks_dir.exists():
            for f in chunks_dir.glob("*"):
                try:
                    f.unlink()
                except:
                    pass
            try:
                chunks_dir.rmdir()
            except:
                pass

        if delete_file_after and file_path.exists():
            try:
                file_path.unlink()
            except:
                pass

        vk.delete(f"transcription_progress:{record_id}")
