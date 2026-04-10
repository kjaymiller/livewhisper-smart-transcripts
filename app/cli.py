import os
import asyncio
import click
from pathlib import Path
from sqlmodel import Session
from app.database import Transcription, engine
from rich.progress import Progress, SpinnerColumn, TextColumn
from app.transcriber import background_transcribe_task


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
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True,
                ) as progress:
                    progress.add_task(
                        description="Processing chunks (auto-splitting if >10m)...",
                        total=None,
                    )
                    await background_transcribe_task(
                        record.id, file_path, delete_file_after=False
                    )

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
