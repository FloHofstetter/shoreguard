"""ShoreGuard database backup — SQLite online snapshot or Postgres pg_dump.

Creates a timestamp-named snapshot of the configured ShoreGuard database in
*target_dir*. SQLite snapshots use the built-in online backup API
(``sqlite3.Connection.backup``) so the running app does not have to stop.
Postgres snapshots shell out to ``pg_dump --format=custom``; ``pg_dump`` and
``pg_restore`` matching the server version must be on ``PATH``.

Typical usage::

    # Snapshot the default database
    uv run python -m scripts.backup --target /var/backups/shoreguard

    # Snapshot an explicit URL (override the Settings singleton)
    uv run python -m scripts.backup \
        --url "$DATABASE_URL" \
        --target ./backups
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from shoreguard.config import default_database_url


def _sqlite_path(database_url: str, parsed) -> Path:
    """Extract the on-disk SQLite path from a SQLAlchemy-style URL.

    ``sqlite:///relative.db``       → ``relative.db``
    ``sqlite:////abs/path/f.db``    → ``/abs/path/f.db``
    """
    if parsed.path:
        return Path(parsed.path)
    return Path(database_url.replace("sqlite:///", "", 1))


def backup(database_url: str, target_dir: Path) -> Path:
    """Back up a ShoreGuard database to *target_dir*.

    Args:
        database_url: SQLAlchemy-style URL, e.g. ``sqlite:///./shoreguard.db``
            or a ``postgresql://`` DSN.
        target_dir: Destination directory; created if missing.

    Returns:
        Path: The created backup file. Filename pattern is
        ``shoreguard-YYYYMMDDTHHMMSSZ.<ext>`` with ``.sqlite`` for SQLite
        and ``.pgdump`` for Postgres.

    Raises:
        ValueError: If the URL scheme is neither SQLite nor Postgres.
        subprocess.CalledProcessError: If ``pg_dump`` exits non-zero.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parsed = urlparse(database_url)

    if parsed.scheme.startswith("sqlite"):
        src_path = _sqlite_path(database_url, parsed)
        dst = target_dir / f"shoreguard-{stamp}.sqlite"
        with sqlite3.connect(src_path) as src, sqlite3.connect(dst) as dst_conn:
            src.backup(dst_conn)
        return dst

    if parsed.scheme.startswith("postgres"):
        dst = target_dir / f"shoreguard-{stamp}.pgdump"
        subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(dst), database_url],
            check=True,
        )
        return dst

    raise ValueError(f"Unsupported database scheme: {parsed.scheme!r}")


def main(argv: list[str] | None = None) -> None:
    """Entry point — parse args, run backup, print the destination path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=None,
        help="Database URL (default: SHOREGUARD_DATABASE_URL or shoreguard config).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Backup target directory (created if missing).",
    )
    args = parser.parse_args(argv)
    url = args.url or os.environ.get("SHOREGUARD_DATABASE_URL") or default_database_url()
    dst = backup(url, args.target)
    print(dst, file=sys.stdout)


if __name__ == "__main__":
    main()
