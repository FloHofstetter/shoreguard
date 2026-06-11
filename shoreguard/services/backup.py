"""Backup and restore for the ShoreGuard management-plane state.

ShoreGuard's state is deliberately small: one SQLite file plus the
secret-key material next to it (session HMAC key, VAPID keypair). A
backup is a tar.gz with a consistent DB snapshot (taken via SQLite's
online backup API — safe while the server runs), the key files, and a
manifest. Restore is CLI-only and requires the server to be stopped.

PostgreSQL deployments are pointed at ``pg_dump`` — wrapping it would
add failure modes without adding value.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from shoreguard.config import shoreguard_config_dir

logger = logging.getLogger(__name__)

KEY_FILES = (".secret_key", ".vapid_private")
ARCHIVE_PREFIX = "shoreguard-backup-"


def _resolve_database_url() -> str:
    """Return the active database URL (engine if initialised, else config).

    Returns:
        str: SQLAlchemy database URL.
    """
    from shoreguard.db import get_engine

    try:
        return str(get_engine().url)
    except RuntimeError:
        from shoreguard.config import default_database_url

        return default_database_url()


def sqlite_db_path(database_url: str) -> Path:
    """Extract the SQLite file path from a database URL.

    Args:
        database_url: SQLAlchemy database URL.

    Returns:
        Path: Filesystem path of the SQLite database.

    Raises:
        ValueError: If the URL is not SQLite (use ``pg_dump`` for
            PostgreSQL) or is an in-memory database.
    """
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        raise ValueError(
            f"Built-in backup supports SQLite only (got {url.drivername}); "
            "for PostgreSQL use pg_dump"
        )
    if not url.database or url.database == ":memory:":
        raise ValueError("In-memory databases cannot be backed up")
    return Path(url.database)


def create_backup(out_dir: Path | None = None, *, database_url: str | None = None) -> Path:
    """Create a backup archive (blocking; call via ``asyncio.to_thread``).

    Propagates ``ValueError`` from :func:`sqlite_db_path` when the
    deployment is not a file-backed SQLite database.

    Args:
        out_dir: Directory for the archive. Defaults to
            ``<config-dir>/backups``.
        database_url: Database URL override (defaults to the active one).

    Returns:
        Path: The created ``.tar.gz`` archive (mode 0600).
    """
    db_path = sqlite_db_path(database_url or _resolve_database_url())
    config_dir = shoreguard_config_dir()
    out_dir = out_dir or (config_dir / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    archive_path = out_dir / f"{ARCHIVE_PREFIX}{stamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "shoreguard.db"
        # SQLite online backup: consistent even while the server writes.
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(snapshot))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        from shoreguard import __version__

        files = ["shoreguard.db"]
        manifest: dict[str, Any] = {
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "tool": "shoreguard",
            "tool_version": __version__,
            "database_file": db_path.name,
            "files": files,
        }

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(snapshot, arcname="shoreguard.db")
            for key_file in KEY_FILES:
                source = config_dir / key_file
                if source.is_file():
                    tar.add(source, arcname=key_file)
                    files.append(key_file)
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            tar.add(manifest_path, arcname="manifest.json")

    os.chmod(archive_path, 0o600)
    logger.info("Backup written to %s (%d bytes)", archive_path, archive_path.stat().st_size)
    return archive_path


def rotate_backups(out_dir: Path, keep: int) -> int:
    """Delete the oldest backup archives beyond the retention count.

    Args:
        out_dir: Directory holding the archives.
        keep: Number of newest archives to keep.

    Returns:
        int: Number of archives deleted.
    """
    archives = sorted(out_dir.glob(f"{ARCHIVE_PREFIX}*.tar.gz"))
    excess = archives[:-keep] if keep > 0 else archives
    for old in excess:
        old.unlink(missing_ok=True)
    if excess:
        logger.info("Rotated %d old backups in %s", len(excess), out_dir)
    return len(excess)


def read_manifest(archive: Path) -> dict[str, Any]:
    """Read and validate the manifest of a backup archive.

    Args:
        archive: Path to the ``.tar.gz`` archive.

    Returns:
        dict[str, Any]: The parsed manifest.

    Raises:
        ValueError: If the archive has no readable manifest.
    """
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.extractfile("manifest.json")
        except KeyError:
            member = None
        if member is None:
            raise ValueError("Not a ShoreGuard backup: manifest.json missing")
        try:
            manifest = json.loads(member.read().decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"Unreadable manifest: {e}") from e
    if manifest.get("tool") != "shoreguard":
        raise ValueError("Not a ShoreGuard backup: unexpected manifest")
    return manifest


def restore_backup(archive: Path, *, database_url: str | None = None) -> list[str]:
    """Restore a backup archive over the current state (server stopped!).

    Existing files are kept as ``<name>.pre-restore`` so a bad restore
    is itself reversible.

    Args:
        archive: Path to the backup archive.
        database_url: Database URL override (defaults to the configured
            one).

    Returns:
        list[str]: The restored file names.

    Raises:
        ValueError: If the archive is invalid or the target is not
            SQLite.
    """
    read_manifest(archive)  # validates
    db_path = sqlite_db_path(database_url or _resolve_database_url())
    config_dir = shoreguard_config_dir()
    restored: list[str] = []

    def _stage_then_replace(source: Path, target: Path) -> None:
        # The temp extraction dir usually lives on /tmp (tmpfs), so a
        # direct rename to the target would fail with EXDEV. Stage a
        # copy NEXT TO the target first, then displace the old file and
        # finish with same-filesystem renames — a failed copy never
        # touches the live file.
        staged = target.with_name(target.name + ".restore-tmp")
        shutil.copy2(source, staged)
        if target.exists():
            target.replace(target.with_name(target.name + ".pre-restore"))
        staged.replace(target)
        os.chmod(target, 0o600)

    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive, "r:gz") as tar:
        tar.extractall(tmp, filter="data")
        tmp_path = Path(tmp)

        snapshot = tmp_path / "shoreguard.db"
        if not snapshot.is_file():
            raise ValueError("Backup contains no database file")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _stage_then_replace(snapshot, db_path)
        restored.append(db_path.name)

        for key_file in KEY_FILES:
            extracted = tmp_path / key_file
            if extracted.is_file():
                _stage_then_replace(extracted, config_dir / key_file)
                restored.append(key_file)

    logger.warning("Restored backup %s → %s", archive, restored)
    return restored
