"""``shoreguard audit`` CLI subcommands.

Offline access to the audit log for operators who cannot reach the web
UI.  The primary use case is exporting the log to a file with a
companion SHA256 manifest so compliance tooling can verify integrity.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json as _json
import os
from pathlib import Path
from typing import Annotated

import typer

audit_app = typer.Typer(
    name="audit",
    help="Audit log operations.",
    no_args_is_help=True,
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@audit_app.command("export")
def audit_export(
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Output file path (will be created with 0600 permissions)",
        ),
    ],
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="json | csv"),
    ] = "json",
    since: Annotated[
        str | None,
        typer.Option(help="ISO-8601 start timestamp filter"),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option(help="ISO-8601 end timestamp filter"),
    ] = None,
    actor: Annotated[
        str | None,
        typer.Option(help="Filter to a single actor"),
    ] = None,
    action: Annotated[
        str | None,
        typer.Option(help="Filter to a single action type"),
    ] = None,
) -> None:
    """Export the audit log with a SHA256 manifest for integrity checks.

    Writes three files alongside *out*:

    - ``<out>`` — the raw export (JSON array or CSV)
    - ``<out>.sha256`` — sha256sum-compatible digest line
    - ``<out>.manifest.json`` — structured metadata (entries, filters,
      generation time, tool version)

    Args:
        out: Output file path (will be created with 0600 permissions).
        fmt: Export format, ``json`` or ``csv``.
        since: ISO-8601 start timestamp filter.
        until: ISO-8601 end timestamp filter.
        actor: Filter to a single actor.
        action: Filter to a single action type.

    Raises:
        typer.Exit: If ``fmt`` is not one of the supported formats.
    """
    # Lazy imports so `shoreguard audit --help` doesn't boot the DB.
    from sqlalchemy.orm import sessionmaker

    from shoreguard.db import get_engine, init_db
    from shoreguard.services.audit import AuditService

    if fmt not in {"json", "csv"}:
        typer.echo(f"Unknown format: {fmt!r}", err=True)
        raise typer.Exit(code=1)

    try:
        engine = get_engine()
    except RuntimeError:
        engine = init_db()

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    svc = AuditService(session_factory)

    if fmt == "json":
        data = svc.export_json(
            actor=actor,
            action=action,
            since=since,
            until=until,
        )
    else:
        data = svc.export_csv(
            actor=actor,
            action=action,
            since=since,
            until=until,
        )

    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(data, encoding="utf-8")
    os.chmod(out, 0o600)

    # Count entries in the export for the manifest
    entries_count: int
    if fmt == "json":
        try:
            entries_count = len(_json.loads(data))
        except _json.JSONDecodeError:
            entries_count = 0
    else:
        # CSV has 1 header row; subtract it if non-empty
        line_count = data.count("\n")
        entries_count = max(0, line_count - 1)

    digest = _sha256_file(out)

    sha_path = out.with_name(out.name + ".sha256")
    sha_path.write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    os.chmod(sha_path, 0o600)

    from shoreguard import __version__

    manifest = {
        "file": out.name,
        "format": fmt,
        "sha256": digest,
        "entries": entries_count,
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "tool": "shoreguard",
        "tool_version": __version__,
        "filter": {
            "actor": actor,
            "action": action,
            "since": since,
            "until": until,
        },
    }
    manifest_path = out.with_name(out.name + ".manifest.json")
    manifest_path.write_text(
        _json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)

    typer.echo(f"Exported {entries_count} audit entries to {out}")
    typer.echo(f"SHA256: {digest}")
    typer.echo(f"Manifest: {manifest_path}")
    typer.echo(f"Verify with: sha256sum -c {sha_path.name}")
