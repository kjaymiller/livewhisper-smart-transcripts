from typing import Optional, List
from datetime import datetime
from sqlmodel import Field, SQLModel, create_engine, Session, Relationship
import os


class Diff(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    correction_id: Optional[int] = Field(default=None, foreign_key="correction.id")
    original_phrase: str
    corrected_phrase: str
    context: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    correction: Optional["Correction"] = Relationship(back_populates="diffs")


class Correction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    transcription_id: Optional[int] = Field(
        default=None, foreign_key="transcription.id"
    )
    corrected_text: str
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    transcription: Optional["Transcription"] = Relationship(
        back_populates="corrections"
    )
    diffs: List[Diff] = Relationship(back_populates="correction")


class Transcription(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    original_text: str = Field(default="")
    status: str = Field(default="completed")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    corrections: List[Correction] = Relationship(back_populates="transcription")


# Connect to the local Postgres database running via Docker
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:password@localhost:5432/transcripts"
)

# Ensure SQLAlchemy uses psycopg3 by rewriting the URL scheme
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
