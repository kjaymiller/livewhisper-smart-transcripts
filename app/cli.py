import os
import re
import click
import httpx
import asyncio
import subprocess
from pathlib import Path
from sqlmodel import Session, select
from app.database import Transcription, Diff, engine
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

# URL of the WhisperLiveKit server we set up
WLK_URL = os.environ.get("WLK_URL", "http://localhost:9090/v1/audio/transcriptions")


def get_audio_duration(file_path: Path) -> float:
    """Uses ffprobe to get the audio file duration in seconds."""
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


def build_prompt_from_corrections(session: Session) -> str:
    """
    Fetch recent diffs and build a prompt string to help Whisper.
    Format: "Glossary/Corrections: [wrong] -> [right], ..."
    """
    # Fetch recent diffs
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


async def async_transcribe_file(file_path: Path):
    """Sends a single file to the WhisperLiveKit and saves to DB."""
    duration = get_audio_duration(file_path)

    try:
        with Session(engine) as session:
            prompt_text = build_prompt_from_corrections(session)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            transient=True,
        ) as progress:
            task_desc = f"Transcribing '{file_path.name}'..."
            if duration > 0:
                task_id = progress.add_task(task_desc, total=duration)
            else:
                task_id = progress.add_task(task_desc, total=None)

            async def do_post():
                async with httpx.AsyncClient(timeout=36000.0) as client:
                    with open(file_path, "rb") as f:
                        data = {"model": "base"}
                        if prompt_text:
                            data["prompt"] = prompt_text

                        return await client.post(
                            WLK_URL,
                            files={"file": (file_path.name, f, "audio/wav")},
                            data=data,
                        )

            post_task = asyncio.create_task(do_post())

            # Background log tailing to update progress
            log_file = Path("wlk.log")
            last_pos = 0
            if log_file.exists():
                last_pos = log_file.stat().st_size

            while not post_task.done():
                if duration > 0 and log_file.exists():
                    current_size = log_file.stat().st_size
                    if current_size > last_pos:
                        try:
                            with open(log_file, "r") as f:
                                f.seek(last_pos)
                                new_data = f.read()
                                last_pos = current_size

                                # Find all last_end values in the new log chunks
                                matches = re.findall(
                                    r"last_end\s*=\s*([\d\.]+)", new_data
                                )
                                if matches:
                                    latest_time = float(matches[-1])
                                    # Ensure we don't go backwards or exceed total
                                    latest_time = min(latest_time, duration)
                                    progress.update(task_id, completed=latest_time)
                        except Exception:
                            pass  # Safely ignore log read errors
                await asyncio.sleep(0.5)

            response = await post_task

            if response.status_code == 200:
                result = response.json()
                original_text = result.get("text", "")

                # Save to DB
                with Session(engine) as session:
                    record = Transcription(
                        filename=file_path.name, original_text=original_text
                    )
                    session.add(record)
                    session.commit()
                    session.refresh(record)

                    click.secho("\n✅ Transcription Complete:", fg="green", bold=True)
                    click.echo("========================================")
                    click.echo(
                        record.original_text.strip()[:500] + "..."
                        if len(record.original_text) > 500
                        else record.original_text.strip()
                    )
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
                click.secho(f"\n❌ Failed: HTTP {response.status_code}", fg="red")
                click.secho(f"   Error: {response.text}\n", fg="red")

    except httpx.ConnectError:
        click.secho(
            "\n❌ Connection Error: Is the Whisper server running? (WLK_URL)\n",
            fg="red",
            bold=True,
        )
    except Exception as e:
        click.secho(f"\n❌ Error processing {file_path.name}: {str(e)}\n", fg="red")


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
