"""CLI interface for s3-folder-sync."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from s3_folder_sync.config import Config
from s3_folder_sync.daemon import SyncDaemon
from s3_folder_sync.state import StateDB
from s3_folder_sync.storage import create_storage_client
from s3_folder_sync.sync_engine import SyncEngine


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def main(verbose: bool) -> None:
    """Sync a local folder to S3-compatible object storage."""
    setup_logging(verbose)


@main.command()
@click.option("--path", default=".", help="Directory to sync")
@click.option("--endpoint", prompt="S3 endpoint URL", help="S3-compatible endpoint")
@click.option("--bucket", prompt="Bucket name", help="S3 bucket name")
@click.option("--prefix", default="", help="Key prefix in bucket")
@click.option("--region", default="us-east-1", help="AWS region")
@click.option("--access-key", default="", help="Access key (or use AWS_ACCESS_KEY_ID env)")
@click.option("--secret-key", default="", help="Secret key (or use AWS_SECRET_ACCESS_KEY env)")
@click.option("--machine-id", default=None, help="Unique machine identifier")
@click.option("--backend", default="s3", type=click.Choice(["s3", "bunny"]), help="Storage backend")
def init(
    path: str,
    endpoint: str,
    bucket: str,
    prefix: str,
    region: str,
    access_key: str,
    secret_key: str,
    machine_id: str | None,
    backend: str,
) -> None:
    """Initialize sync configuration for a directory."""
    watch_path = Path(path).resolve()
    if not watch_path.is_dir():
        click.echo(f"Error: {watch_path} is not a directory", err=True)
        sys.exit(1)

    config = Config.create(
        watch_path=str(watch_path),
        endpoint=endpoint,
        bucket=bucket,
        prefix=prefix,
        region=region,
        access_key=access_key,
        secret_key=secret_key,
        machine_id=machine_id,
        backend=backend,
    )

    click.echo(f"Initialized s3-folder-sync in {config.sync_dir}")
    click.echo(f"  Backend: {config.storage.backend}")
    click.echo(f"  Machine ID: {config.machine.id}")
    click.echo(f"  Bucket: {config.storage.bucket}")
    click.echo(f"  Prefix: {config.storage.prefix or '(none)'}")


@main.command()
@click.option("--path", default=".", help="Synced directory")
@click.option("-d", "--daemon", "background", is_flag=True, help="Run in background")
def start(path: str, background: bool) -> None:
    """Start the sync daemon."""
    config = _load_config(path)

    running, pid = SyncDaemon.is_running(config)
    if running:
        click.echo(f"Daemon already running (PID {pid})")
        sys.exit(1)

    daemon = SyncDaemon(config)
    if background:
        click.echo(f"Starting sync daemon in background for {config.watch_path}")
    else:
        click.echo(f"Starting sync daemon for {config.watch_path} (Ctrl+C to stop)")

    daemon.start(foreground=not background)


@main.command()
@click.option("--path", default=".", help="Synced directory")
def stop(path: str) -> None:
    """Stop the sync daemon."""
    config = _load_config(path)
    if SyncDaemon.stop_daemon(config):
        click.echo("Daemon stopped")
    else:
        click.echo("No running daemon found")


@main.command()
@click.option("--path", default=".", help="Synced directory")
def status(path: str) -> None:
    """Show sync status."""
    config = _load_config(path)

    running, pid = SyncDaemon.is_running(config)
    click.echo(f"Watch path: {config.watch_path}")
    click.echo(f"Machine ID: {config.machine.id}")
    click.echo(f"Bucket: {config.storage.bucket}")
    click.echo(f"Daemon: {'running (PID %d)' % pid if running else 'stopped'}")

    db = StateDB(config.db_path)
    states = db.get_all()
    db.close()

    synced = sum(1 for s in states if not s.is_deleted)
    deleted = sum(1 for s in states if s.is_deleted)
    click.echo(f"Tracked files: {synced} synced, {deleted} pending delete")


@main.command(name="sync")
@click.option("--path", default=".", help="Synced directory")
def sync_now(path: str) -> None:
    """Force an immediate sync cycle."""
    config = _load_config(path)
    client = create_storage_client(config)
    db = StateDB(config.db_path)
    engine = SyncEngine(config, client, db)

    click.echo("Running sync cycle...")
    conflicts = engine.run_cycle()
    db.close()

    if conflicts:
        click.echo(f"Conflicts ({len(conflicts)}):")
        for c in conflicts:
            click.echo(f"  {c}")
    else:
        click.echo("Sync complete, no conflicts")


@main.command()
@click.option("--path", default=".", help="Synced directory")
def conflicts(path: str) -> None:
    """List conflict files."""
    watch_path = Path(path).resolve()
    conflict_files = []

    for f in watch_path.rglob("*.conflict.*"):
        if f.is_file():
            try:
                relative = str(f.relative_to(watch_path))
                conflict_files.append(relative)
            except ValueError:
                pass

    if not conflict_files:
        click.echo("No conflict files found")
    else:
        click.echo(f"Conflict files ({len(conflict_files)}):")
        for cf in sorted(conflict_files):
            click.echo(f"  {cf}")


@main.command()
@click.option("--path", default=".", help="Synced directory")
def menubar(path: str) -> None:
    """Launch the macOS menu bar app."""
    try:
        from s3_folder_sync.menubar import run_menubar
    except ImportError:
        click.echo(
            "Menu bar support requires rumps. Install with:\n"
            "  pip install 's3-folder-sync[menubar]'",
            err=True,
        )
        sys.exit(1)

    run_menubar(str(Path(path).resolve()))


def _load_config(path: str) -> Config:
    try:
        return Config.load(Path(path).resolve())
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
