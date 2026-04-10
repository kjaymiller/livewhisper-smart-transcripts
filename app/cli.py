import os
import click
import httpx
import asyncio
from pathlib import Path
from sqlmodel import Session, select
from app.database import get_session, TranscriptionRecord, engine

# URL of the WhisperLiveKit server we set up
WLK_URL = os.environ.get("WLK_URL", "http://localhost:9090/v1/audio/transcriptions")


def build_prompt_from_corrections(session: Session) -> str:
    """
    Fetch recent corrections and build a prompt string to help Whisper.
    Format: "Glossary/Corrections: [wrong] -> [right], ..."
    """
    records = session.exec(
        select(TranscriptionRecord)
        .where(TranscriptionRecord.corrected_text != None)
        .order_by(TranscriptionRecord.updated_at.desc())
        .limit(20)
    ).all()

    corrections = []
    for r in records:
        if r.original_text.strip() != r.corrected_text.strip():
            corrections.append(r.corrected_text.strip())

    if not corrections:
        return ""

    return (
        "Make sure to use correct terminology based on previous corrections: "
        + " ".join(corrections)
    )


async def async_transcribe_file(file_path: Path):
    """Sends a single file to the WhisperLiveKit and saves to DB."""
    click.secho(f"⏳ Transcribing '{file_path.name}'...", fg="yellow")

    try:
        with Session(engine) as session:
            prompt_text = build_prompt_from_corrections(session)

        async with httpx.AsyncClient(timeout=600.0) as client:
            with open(file_path, "rb") as f:
                data = {"model": "base"}
                if prompt_text:
                    data["prompt"] = prompt_text

                response = await client.post(
                    WLK_URL,
                    files={"file": (file_path.name, f, "audio/wav")},
                    data=data,
                )

                if response.status_code == 200:
                    result = response.json()
                    original_text = result.get("text", "")

                    # Save to DB
                    with Session(engine) as session:
                        record = TranscriptionRecord(
                            filename=file_path.name, original_text=original_text
                        )
                        session.add(record)
                        session.commit()
                        session.refresh(record)

                        click.secho(
                            "\n✅ Transcription Complete:", fg="green", bold=True
                        )
                        click.echo("========================================")
                        click.echo(record.original_text.strip())
                        click.echo("========================================")
                        click.secho(
                            f"Saved to database with Record ID: {record.id}", dim=True
                        )
                        app_port = os.environ.get("APP_PORT", "8000")
                        click.secho(
                            f"You can view and correct this in the Web UI at http://localhost:{app_port}\n",
                            dim=True,
                        )
                else:
                    click.secho(f"❌ Failed: HTTP {response.status_code}", fg="red")
                    click.secho(f"   Error: {response.text}\n", fg="red")
    except httpx.ConnectError:
        click.secho(
            "❌ Connection Error: Is the Whisper server running? (WLK_URL)\n",
            fg="red",
            bold=True,
        )
    except Exception as e:
        click.secho(f"❌ Error processing {file_path.name}: {str(e)}\n", fg="red")


@click.command(
    help="Transcribe individual audio file(s) using the Transcription Improvement Tool."
)
@click.argument(
    "files", nargs=-1, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def main(files):
    from app.database import create_db_and_tables

    create_db_and_tables()

    if not files:
        click.echo(click.get_current_context().get_help())
        return

    for file_path in files:
        asyncio.run(async_transcribe_file(file_path))


if __name__ == "__main__":
    main()
