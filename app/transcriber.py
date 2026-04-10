import os
import json
import asyncio
from pathlib import Path
from sqlmodel import Session, select
import valkey

from app.database import engine, Transcription, Diff
from whisperlivekit.test_client import transcribe_audio

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")

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

        url = os.environ.get("WLK_WS_URL", "ws://localhost:9090/asr")
        # Ensure we pass en if we want english to avoid detection latency or switching
        # Wait, test_client connects to WLK WebSocket. We'll use speed=0 to process as fast as possible.

        result = await transcribe_audio(
            audio_path=str(file_path),
            url=url,
            chunk_duration=0.5,
            speed=0,  # Process as fast as possible
            timeout=3600.0,  # Long timeout for full files
            on_response=on_response_callback,
            mode="full",
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
