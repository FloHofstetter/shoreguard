"""ShoreGuard database restore — counterpart to scripts/backup.py.

Restores a snapshot produced by ``scripts.backup`` into the configured
ShoreGuard database. SQLite restore overwrites the target file using the
online backup API (so callers don't need to stop the app, though they should
drain it first). Postgres restore calls ``pg_restore --clean --if-exists
--format=custom``; the target database must already exist.

Typical usage::

    uv run python -m scripts.restore \
        --source /var/backups/shoreguard/shoreguard-20260410T120000Z.sqlite \
        --url sqlite:///./shoreguard.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from shoreguard.config import default_database_url


def _sqlite_path(database_url: str, parsed) -> Path:
    """See ``scripts.backup._sqlite_path``."""
    if parsed.path:
        return Path(parsed.path)
    return Path(database_url.replace("sqlite:///", "", 1))


def restore(source: Path, database_url: str) -> None:
    """Restore a backup file into the ShoreGuard database.

    Args:
        source: Backup file produced by ``scripts.backup``. Suffix decides
            the restore method (``.sqlite`` → SQLite online backup,
            ``.pgdump`` → ``pg_restore``).
        database_url: Target database URL.

    Raises:
        FileNotFoundError: If *source* does not exist.
        ValueError: If the URL scheme does not match the backup file type.
        subprocess.CalledProcessError: If ``pg_restore`` exits non-zero.
    """
    if not source.exists():
        raise FileNotFoundError(f"Backup file not found: {source}")

    parsed = urlparse(database_url)

    if parsed.scheme.startswith("sqlite"):
        dst_path = _sqlite_path(database_url, parsed)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove any existing DB so the restore is a clean overwrite.
        if dst_path.exists():
            dst_path.unlink()
        with sqlite3.connect(source) as src, sqlite3.connect(dst_path) as dst_conn:
            src.backup(dst_conn)
        return

    if parsed.scheme.startswith("postgres"):
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--format=custom",
                "--dbname",
                database_url,
                str(source),
            ],
            check=True,
        )
        return

    raise ValueError(f"Unsupported database scheme: {parsed.scheme!r}")


def main(argv: list[str] | None = None) -> None:
    """Entry point — parse args and run restore."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Backup file produced by scripts.backup.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Target database URL (default: SHOREGUARD_DATABASE_URL or shoreguard config).",
    )
    args = parser.parse_args(argv)
    url = args.url or os.environ.get("SHOREGUARD_DATABASE_URL") or default_database_url()
    restore(args.source, url)
    print(f"Restored {args.source} → {url}", file=sys.stdout)


if __name__ == "__main__":
    main()
