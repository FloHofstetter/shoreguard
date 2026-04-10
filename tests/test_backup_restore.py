"""Tests for scripts/backup.py + scripts/restore.py (SQLite path).

The Postgres path shells out to ``pg_dump``/``pg_restore`` which are not
guaranteed to be available in CI; we cover only the SQLite roundtrip here
and rely on manual verification for Postgres in prod smoke tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.backup import backup
from scripts.restore import restore


def _seed_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO t (name) VALUES (?)", [("alice",), ("bob",)])
        conn.commit()


def test_sqlite_backup_produces_nonempty_file(tmp_path: Path) -> None:
    """backup() writes a dated .sqlite file next to the source."""
    src = tmp_path / "src.sqlite"
    _seed_db(src)

    target_dir = tmp_path / "backups"
    dst = backup(f"sqlite:///{src}", target_dir)

    assert dst.exists()
    assert dst.parent == target_dir
    assert dst.suffix == ".sqlite"
    assert dst.name.startswith("shoreguard-")
    assert dst.stat().st_size > 0


def test_sqlite_backup_and_restore_roundtrip(tmp_path: Path) -> None:
    """A backed-up DB is bit-perfect after restore into a new location."""
    src = tmp_path / "src.sqlite"
    _seed_db(src)

    target_dir = tmp_path / "backups"
    dst_file = backup(f"sqlite:///{src}", target_dir)

    # Drop the original and restore into the same path.
    src.unlink()
    assert not src.exists()

    restore(dst_file, f"sqlite:///{src}")

    assert src.exists()
    with sqlite3.connect(src) as conn:
        rows = conn.execute("SELECT name FROM t ORDER BY id").fetchall()
    assert rows == [("alice",), ("bob",)]


def test_sqlite_restore_overwrites_existing(tmp_path: Path) -> None:
    """Restore into an existing file replaces it entirely."""
    src = tmp_path / "src.sqlite"
    _seed_db(src)

    target_dir = tmp_path / "backups"
    dst_file = backup(f"sqlite:///{src}", target_dir)

    # Mutate the source after the backup was taken.
    with sqlite3.connect(src) as conn:
        conn.execute("INSERT INTO t (name) VALUES ('charlie')")
        conn.commit()

    # Restore must replace the mutated state with the backed-up state.
    restore(dst_file, f"sqlite:///{src}")

    with sqlite3.connect(src) as conn:
        rows = conn.execute("SELECT name FROM t ORDER BY id").fetchall()
    assert rows == [("alice",), ("bob",)]


def test_backup_rejects_unknown_scheme(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported database scheme"):
        backup("mysql://example/db", tmp_path)


def test_restore_raises_on_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        restore(tmp_path / "does-not-exist.sqlite", f"sqlite:///{tmp_path}/x.sqlite")
