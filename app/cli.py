import os
import json
import asyncio
import click
from pathlib import Path
from sqlmodel import Session
import valkey
from app.database import Transcription, engine
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from app.transcriber import background_transcribe_task

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
vk = valkey.from_url(VALKEY_URL, decode_responses=True)


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
        with Session(engine) as session:
            record = Transcription(
                filename=file_path.name, original_text="", status="processing"
            )
            session.add(record)
            session.commit()
            session.refresh(record)

            click.secho(
                f"\n⏳ Starting transcription for '{file_path.name}'...", fg="yellow"
            )

            async def run_task():
                # We'll run the background task but also spawn a polling loop to update the rich progress bar
                task = asyncio.create_task(
                    background_transcribe_task(
                        record.id, file_path, delete_file_after=False
                    )
                )

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    transient=True,
                ) as progress:
                    # Initially, we don't know total chunks
                    progress_task_id = progress.add_task(
                        description="Preparing chunks...", total=100, completed=0
                    )

                    while not task.done():
                        try:
                            progress_data = vk.get(
                                f"transcription_progress:{record.id}"
                            )
                            if progress_data:
                                p_dict = json.loads(progress_data)
                                current = p_dict.get("current", 0)
                                total = p_dict.get("total", 0)

                                if total > 0:
                                    percent_complete = (current / total) * 100
                                    progress.update(
                                        progress_task_id,
                                        description=f"Processing chunk {current} of {total}...",
                                        completed=percent_complete,
                                    )
                        except Exception:
                            pass  # Safely ignore JSON or connection errors during polling

                        await asyncio.sleep(1)  # Poll every second

                await task  # Ensure it finishes

            asyncio.run(run_task())

            # Check result
            session.refresh(record)
            if record.status == "completed":
                click.secho("\n✅ Transcription Complete:", fg="green", bold=True)
                click.echo("========================================")
                click.echo(
                    record.original_text.strip()[:500] + "..."
                    if len(record.original_text) > 500
                    else record.original_text.strip()
                )
                click.echo("========================================")
                click.secho(f"Saved to database with Record ID: {record.id}", dim=True)
                app_port = os.environ.get("APP_PORT", "8000")
                click.secho(
                    f"You can view and correct this in the Web UI at http://localhost:{app_port}\n",
                    dim=True,
                )
            else:
                click.secho(f"\n❌ Failed to transcribe '{file_path.name}'.", fg="red")


if __name__ == "__main__":
    main()
