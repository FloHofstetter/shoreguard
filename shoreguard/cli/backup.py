"""``shoreguard backup`` CLI subcommands.

Backups bundle the SQLite database (consistent online snapshot), the
secret-key material, and a manifest into one tar.gz. Restore requires
the server to be stopped and keeps the replaced files as
``*.pre-restore`` so it is itself reversible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

backup_app = typer.Typer(
    name="backup",
    help="Backup and restore the ShoreGuard state.",
    no_args_is_help=True,
)


@backup_app.command("create")
def backup_create(
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help="Target directory (default: <config-dir>/backups)",
        ),
    ] = None,
    keep: Annotated[
        int | None,
        typer.Option(help="Rotate: keep only the newest N archives in the target directory"),
    ] = None,
) -> None:
    """Create a backup archive of the database and key material.

    Args:
        out: Target directory for the archive.
        keep: Optional rotation — keep only the newest N archives.

    Raises:
        typer.Exit: With code 1 when the deployment is not SQLite-backed.
    """
    from shoreguard.services.backup import create_backup, rotate_backups

    try:
        path = create_backup(out)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Backup written: {path}")
    typer.echo("The archive contains the secret key material — store it like a credential.")
    if keep is not None:
        deleted = rotate_backups(path.parent, keep)
        if deleted:
            typer.echo(f"Rotated {deleted} old archive(s).")


@backup_app.command("restore")
def backup_restore(
    archive: Annotated[Path, typer.Argument(help="Backup archive (.tar.gz) to restore")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt"),
    ] = False,
) -> None:
    """Restore a backup archive over the current state.

    Stop the ShoreGuard server first — restoring under a running server
    corrupts the database. Replaced files are kept as
    ``*.pre-restore``.

    Args:
        archive: The backup archive to restore.
        yes: Skip the confirmation prompt.

    Raises:
        typer.Exit: With code 1 on an invalid archive or declined
            confirmation.
    """
    from shoreguard.services.backup import read_manifest, restore_backup

    archive = archive.expanduser().resolve()
    if not archive.is_file():
        typer.echo(f"Error: {archive} does not exist", err=True)
        raise typer.Exit(code=1)
    try:
        manifest = read_manifest(archive)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Archive created: {manifest.get('created_at')}")
    typer.echo(f"Tool version:    {manifest.get('tool_version')}")
    typer.echo(f"Files:           {', '.join(manifest.get('files', []))}")
    if not yes:
        typer.echo("Make sure the ShoreGuard server is STOPPED before restoring.")
        if not typer.confirm("Overwrite the current database and key material?"):
            raise typer.Exit(code=1)

    try:
        restored = restore_backup(archive)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Restored: {', '.join(restored)}")
    typer.echo("Previous files kept as *.pre-restore next to the restored ones.")
