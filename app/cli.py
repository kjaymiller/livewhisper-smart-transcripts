import os
import json
import asyncio
import click
import httpx
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
from rich.console import Console
from rich.table import Table
from app.transcriber import background_transcribe_task

VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
APP_PORT = os.environ.get("APP_PORT", "8000")
vk = valkey.from_url(VALKEY_URL, decode_responses=True)
console = Console()


@click.group(help="Transcription Improvement Tool CLI")
def cli():
    pass


@cli.command(
    help="Transcribe individual audio file(s) using the Transcription Improvement Tool."
)
@click.argument(
    "files", nargs=-1, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def transcribe(files):
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
                f"\n⏳ Starting transcription for '{file_path.name}' (ID: {record.id})...",
                fg="yellow",
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
                    transient=True,
                ) as progress:
                    # We don't have chunks anymore, we have a continuous stream.
                    progress_task_id = progress.add_task(
                        description=f"[ID: {record.id}] Connecting to streaming API...",
                        total=None,
                    )

                    while not task.done():
                        try:
                            progress_data = vk.get(
                                f"transcription_progress:{record.id}"
                            )
                            if progress_data:
                                p_dict = json.loads(progress_data)
                                text = p_dict.get("text", "")

                                # Show a live sample of the transcription
                                preview = ""
                                if text:
                                    clean_text = text.replace("\n", " ")
                                    preview = (
                                        clean_text[-50:]
                                        if len(clean_text) > 50
                                        else clean_text
                                    )

                                progress.update(
                                    progress_task_id,
                                    description=f"[ID: {record.id}] Transcribing: ...{preview}"
                                    if preview
                                    else f"[ID: {record.id}] Transcribing...",
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
                click.secho(
                    f"You can view and correct this in the Web UI at http://localhost:{APP_PORT}\n",
                    dim=True,
                )
            else:
                click.secho(f"\n❌ Failed to transcribe '{file_path.name}'.", fg="red")


@cli.command(name="active", help="List all active, in-progress transcriptions")
def list_active():
    """Fetch active transcriptions from the Valkey API endpoint."""
    url = f"http://localhost:{APP_PORT}/api/transcriptions/active"

    try:
        response = httpx.get(url, timeout=10)
        if response.status_code == 200:
            active_jobs = response.json()

            if not active_jobs:
                console.print(
                    "[yellow]No active transcriptions currently processing.[/yellow]"
                )
                return

            table = Table(
                title="Active Transcriptions",
                show_header=True,
                header_style="bold magenta",
            )
            table.add_column("Record ID", style="cyan", width=12)
            table.add_column("Text Sample", style="green")

            for job in active_jobs:
                table.add_row(
                    str(job.get("id")), job.get("sample", "(Initializing...)").strip()
                )

            console.print(table)
        else:
            console.print(
                f"[red]Error fetching active transcriptions: HTTP {response.status_code}[/red]"
            )
    except httpx.ConnectError:
        console.print(
            f"[red]Error: Could not connect to Web API at {url}. Is the docker app container running?[/red]"
        )


if __name__ == "__main__":
    cli()
