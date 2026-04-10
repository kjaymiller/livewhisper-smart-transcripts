import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
import difflib

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select
import valkey

from app.database import (
    create_db_and_tables,
    get_session,
    Transcription,
    Correction,
    Diff,
)
from app.transcriber import background_transcribe_task

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
TEMP_DIR = Path("/tmp/conduit_audio")

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
vk = valkey.from_url(VALKEY_URL, decode_responses=True)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    TEMP_DIR.mkdir(exist_ok=True, parents=True)


# Ensure static directory exists
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def read_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/transcribe")
async def transcribe_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    # Save file temporarily
    file_path = TEMP_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Create database record with "processing" status
    record = Transcription(
        filename=file.filename, original_text="", status="processing"
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    # Trigger background task
    background_tasks.add_task(
        background_transcribe_task, record.id, file_path, delete_file_after=True
    )

    return {"id": record.id, "message": "Transcription started in background"}


@app.get("/api/transcriptions/{record_id}/status")
def get_transcription_status(record_id: int, session: Session = Depends(get_session)):
    record = session.get(Transcription, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Transcription not found")

    result = {
        "id": record.id,
        "status": record.status,
    }

    if record.status == "completed":
        result["original_text"] = record.original_text
    elif record.status == "processing":
        try:
            progress_data = vk.get(f"transcription_progress:{record_id}")
            if progress_data:
                progress = json.loads(progress_data)
                result["current_chunk"] = progress.get("current", 0)
                result["total_chunks"] = progress.get("total", 0)
                result["partial_text"] = progress.get("text", "")
        except Exception as e:
            pass

    return result


class CorrectionUpdate(BaseModel):
    corrected_text: str


@app.put("/api/transcriptions/{record_id}")
def update_transcription(
    record_id: int, update: CorrectionUpdate, session: Session = Depends(get_session)
):
    record = session.get(Transcription, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Transcription not found")

    correction = Correction(
        transcription_id=record.id,
        corrected_text=update.corrected_text,
        status="accepted",
    )
    session.add(correction)
    session.commit()
    session.refresh(correction)

    # Compute diffs
    original_words = record.original_text.split()
    corrected_words = update.corrected_text.split()

    matcher = difflib.SequenceMatcher(None, original_words, corrected_words)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert", "delete"):
            orig_phrase = " ".join(original_words[i1:i2])
            corr_phrase = " ".join(corrected_words[j1:j2])

            # Extract surrounding context (3 words before and after)
            ctx_start = max(0, i1 - 3)
            ctx_end = min(len(original_words), i2 + 3)
            context = " ".join(original_words[ctx_start:ctx_end])

            diff_entry = Diff(
                correction_id=correction.id,
                original_phrase=orig_phrase,
                corrected_phrase=corr_phrase,
                context=context,
            )
            session.add(diff_entry)

    session.commit()

    return {"id": record.id, "message": "Correction and diffs added"}


@app.get("/api/transcriptions/{record_id}")
def get_transcription(record_id: int, session: Session = Depends(get_session)):
    t = session.get(Transcription, record_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transcription not found")

    corrections = [c for c in t.corrections if c.status == "accepted"]
    latest_correction = (
        sorted(corrections, key=lambda x: x.created_at, reverse=True)[0]
        if corrections
        else None
    )

    record_data = {
        "id": t.id,
        "filename": t.filename,
        "status": t.status,
        "created_at": t.created_at,
    }

    if t.status == "completed":
        record_data["original_text"] = t.original_text
        record_data["corrected_text"] = (
            latest_correction.corrected_text if latest_correction else None
        )

    return record_data


@app.delete("/api/transcriptions/{record_id}")
def delete_transcription(record_id: int, session: Session = Depends(get_session)):
    record = session.get(Transcription, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Transcription not found")

    # Manually delete related corrections and diffs because we didn't setup cascade deletes
    for correction in record.corrections:
        for diff in correction.diffs:
            session.delete(diff)
        session.delete(correction)

    session.delete(record)
    session.commit()
    return {"message": "Transcription deleted successfully"}


@app.get("/api/transcriptions")
def list_transcriptions(session: Session = Depends(get_session)):
    transcriptions = session.exec(
        select(Transcription).order_by(Transcription.created_at.desc())
    ).all()

    results = []
    for t in transcriptions:
        corrections = [c for c in t.corrections if c.status == "accepted"]
        latest_correction = (
            sorted(corrections, key=lambda x: x.created_at, reverse=True)[0]
            if corrections
            else None
        )

        record_data = {
            "id": t.id,
            "filename": t.filename,
            "status": t.status,
            "created_at": t.created_at,
        }

        if t.status == "completed":
            record_data["original_text"] = t.original_text
            record_data["corrected_text"] = (
                latest_correction.corrected_text if latest_correction else None
            )

        results.append(record_data)

    return results
