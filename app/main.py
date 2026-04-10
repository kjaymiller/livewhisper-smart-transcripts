import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import create_db_and_tables, get_session, TranscriptionRecord

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
    record = session.get(TranscriptionRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Transcription not found")

    record.corrected_text = update.corrected_text
    record.updated_at = datetime.utcnow()

    session.add(record)
    session.commit()
    session.refresh(record)
    return record


@app.get("/api/transcriptions")
def list_transcriptions(session: Session = Depends(get_session)):
    return session.exec(
        select(TranscriptionRecord).order_by(TranscriptionRecord.created_at.desc())
    ).all()
