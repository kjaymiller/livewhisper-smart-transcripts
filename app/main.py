import os
from pathlib import Path
from datetime import datetime
from typing import Optional
import difflib

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import (
    create_db_and_tables,
    get_session,
    Transcription,
    Correction,
    Diff,
)

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


# Ensure static directory exists
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def read_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


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

        results.append(
            {
                "id": t.id,
                "filename": t.filename,
                "original_text": t.original_text,
                "corrected_text": latest_correction.corrected_text
                if latest_correction
                else None,
                "created_at": t.created_at,
            }
        )
    return results
