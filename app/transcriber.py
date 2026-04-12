import os
import json
import asyncio
import logging
import traceback
from pathlib import Path
from sqlmodel import Session, select
import valkey
import mlx_whisper
import torch
from pyannote.audio import Pipeline
from dotenv import load_dotenv

from app.database import engine, Transcription, Diff

load_dotenv()

logger = logging.getLogger(__name__)

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
HF_KEY = os.environ.get("HUGGINGFACE_API_KEY")

if not HF_KEY:
    logger.warning(
        "HUGGINGFACE_API_KEY not found in environment. Pyannote diarization might fail."
    )

# Setup Valkey client
vk = valkey.from_url(VALKEY_URL, decode_responses=True)


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


def align_words_with_diarization(whisper_result: dict, diarization) -> str:
    """
    Aligns whisper word-level output with pyannote diarization output
    by checking the midpoint of each word's timestamp.
    """
    text_blocks = []
    current_speaker = None
    current_words = []

    def flush_block():
        nonlocal current_speaker, current_words
        if current_words:
            speaker_label = current_speaker if current_speaker else "UNKNOWN"
            text = "".join(current_words).strip()
            text_blocks.append(f"[{speaker_label}]: {text}")
            current_words = []

    segments = whisper_result.get("segments", [])

    for segment in segments:
        words = segment.get("words", [])
        if not words:
            # Fallback if words are missing (shouldn't happen with word_timestamps=True)
            word_midpoint = (segment["start"] + segment["end"]) / 2
            detected_speaker = "UNKNOWN"
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                if turn.start <= word_midpoint <= turn.end:
                    detected_speaker = speaker
                    break

            if detected_speaker != current_speaker:
                flush_block()
                current_speaker = detected_speaker

            current_words.append(segment.get("text", " "))
            continue

        for word in words:
            word_start = word["start"]
            word_end = word["end"]
            word_text = word["word"]

            word_midpoint = (word_start + word_end) / 2

            # Find speaker for this word
            detected_speaker = "UNKNOWN"
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                if turn.start <= word_midpoint <= turn.end:
                    detected_speaker = speaker
                    break

            if detected_speaker != current_speaker:
                flush_block()
                current_speaker = detected_speaker

            current_words.append(word_text)

    flush_block()

    return "\n\n".join(text_blocks)


async def background_transcribe_task(
    record_id: int, file_path: Path, delete_file_after: bool = False
):
    try:
        vk.setex(
            f"transcription_progress:{record_id}",
            86400,
            json.dumps({"stage": "Initializing...", "text": ""}),
        )

        with Session(engine) as session:
            prompt_text = build_prompt_from_corrections(session)

        # 1. Diarization
        vk.setex(
            f"transcription_progress:{record_id}",
            86400,
            json.dumps({"stage": "Diarizing audio (PyAnnote)...", "text": ""}),
        )
        logger.info(f"Starting diarization for {file_path}")

        # Load pipeline synchronously in an executor if needed, but it's okay here for a background worker.
        pipeline = await asyncio.to_thread(
            Pipeline.from_pretrained,
            "pyannote/speaker-diarization-community-1",
            token=HF_KEY,
        )

        if torch.backends.mps.is_available():
            pipeline.to(torch.device("mps"))
        elif torch.cuda.is_available():
            pipeline.to(torch.device("cuda"))

        diarization = await asyncio.to_thread(
            pipeline, str(file_path), min_speakers=1, max_speakers=4
        )

        # 2. Transcription
        vk.setex(
            f"transcription_progress:{record_id}",
            86400,
            json.dumps({"stage": "Transcribing audio (MLX Whisper)...", "text": ""}),
        )
        logger.info(f"Starting transcription for {file_path}")

        whisper_kwargs = {"word_timestamps": True, "verbose": False}
        if prompt_text:
            whisper_kwargs["initial_prompt"] = prompt_text

        whisper_result = await asyncio.to_thread(
            mlx_whisper.transcribe, str(file_path), **whisper_kwargs
        )

        # 3. Alignment
        vk.setex(
            f"transcription_progress:{record_id}",
            86400,
            json.dumps({"stage": "Aligning speakers and text...", "text": ""}),
        )
        logger.info(f"Starting alignment for {file_path}")

        final_text = await asyncio.to_thread(
            align_words_with_diarization, whisper_result, diarization
        )

        vk.setex(
            f"transcription_progress:{record_id}",
            86400,
            json.dumps({"stage": "Completed", "text": final_text}),
        )

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
        if delete_file_after and file_path.exists():
            try:
                file_path.unlink()
            except:
                pass
        vk.delete(f"transcription_progress:{record_id}")
