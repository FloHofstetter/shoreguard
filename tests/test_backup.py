"""Tests for backup create / rotate / restore and the download route."""

from __future__ import annotations

import sqlite3
import tarfile

import pytest

from shoreguard.services.backup import (
    create_backup,
    read_manifest,
    restore_backup,
    rotate_backups,
    sqlite_db_path,
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = tmp_path / "shoreguard"
    d.mkdir()
    return d


@pytest.fixture
def db_file(config_dir):
    path = config_dir / "shoreguard.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('original')")
    conn.commit()
    conn.close()
    return path


def test_sqlite_db_path_rejects_postgres() -> None:
    with pytest.raises(ValueError, match="pg_dump"):
        sqlite_db_path("postgresql://u:p@localhost/db")


def test_sqlite_db_path_rejects_memory() -> None:
    with pytest.raises(ValueError, match="memory"):
        sqlite_db_path("sqlite://")


def test_create_backup_bundles_db_and_keys(config_dir, db_file) -> None:
    (config_dir / ".secret_key").write_bytes(b"k" * 32)
    (config_dir / ".vapid_private").write_text("pem")

    archive = create_backup(database_url=f"sqlite:///{db_file}")

    assert archive.parent == config_dir / "backups"
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert names == {"shoreguard.db", ".secret_key", ".vapid_private", "manifest.json"}
    manifest = read_manifest(archive)
    assert manifest["tool"] == "shoreguard"


def test_create_backup_without_key_files(config_dir, db_file) -> None:
    archive = create_backup(database_url=f"sqlite:///{db_file}")
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert names == {"shoreguard.db", "manifest.json"}


def test_rotate_backups_keeps_newest(config_dir, db_file) -> None:
    out = config_dir / "backups"
    real = create_backup(database_url=f"sqlite:///{db_file}")
    # Same-second snapshots share a name; fabricate older archives instead.
    (out / "shoreguard-backup-20200101-000000.tar.gz").write_bytes(b"old")
    (out / "shoreguard-backup-20200101-000001.tar.gz").write_bytes(b"old")

    deleted = rotate_backups(out, keep=1)

    remaining = [p.name for p in out.glob("*.tar.gz")]
    assert deleted == 2
    assert remaining == [real.name]


def test_restore_round_trip(config_dir, db_file) -> None:
    (config_dir / ".secret_key").write_bytes(b"k" * 32)
    url = f"sqlite:///{db_file}"
    archive = create_backup(database_url=url)

    # Mutate the live state after the backup.
    conn = sqlite3.connect(str(db_file))
    conn.execute("UPDATE t SET v = 'changed'")
    conn.commit()
    conn.close()
    (config_dir / ".secret_key").write_bytes(b"x" * 32)

    restored = restore_backup(archive, database_url=url)

    assert "shoreguard.db" in restored and ".secret_key" in restored
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT v FROM t").fetchone()[0] == "original"
    conn.close()
    assert (config_dir / ".secret_key").read_bytes() == b"k" * 32
    # The replaced files survive as .pre-restore.
    assert (config_dir / "shoreguard.db.pre-restore").exists()
    assert (config_dir / ".secret_key.pre-restore").read_bytes() == b"x" * 32


def test_read_manifest_rejects_garbage(tmp_path) -> None:
    bogus = tmp_path / "x.tar.gz"
    with tarfile.open(bogus, "w:gz") as tar:
        f = tmp_path / "other.txt"
        f.write_text("hi")
        tar.add(f, arcname="other.txt")
    with pytest.raises(ValueError, match="manifest"):
        read_manifest(bogus)


# ─── Route ───────────────────────────────────────────────────────────────────


@pytest.fixture
def db(config_dir):
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_backup_route_requires_admin(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "viewer@test.com", "password": "viewerpass1"}
        )
        assert resp.status_code == 200
        resp = await client.get("/api/system/backup")
        assert resp.status_code == 403


async def test_backup_route_streams_archive(db, monkeypatch, tmp_path) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    # Point the backup at a real SQLite file regardless of test engine state.
    db_path = tmp_path / "live.db"
    sqlite3.connect(str(db_path)).close()
    monkeypatch.setattr(
        "shoreguard.services.backup._resolve_database_url",
        lambda: f"sqlite:///{db_path}",
    )

    await create_user("admin@test.com", "adminpassword1", "admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "admin@test.com", "password": "adminpassword1"}
        )
        assert resp.status_code == 200
        resp = await client.get("/api/system/backup")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"
        assert "shoreguard-backup-" in resp.headers.get("content-disposition", "")
        assert len(resp.content) > 0


def test_restore_failure_keeps_live_db(config_dir, db_file, monkeypatch) -> None:
    """A failing restore copy must never displace the live database."""
    import shutil as _shutil

    url = f"sqlite:///{db_file}"
    archive = create_backup(database_url=url)

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("shoreguard.services.backup.shutil.copy2", _boom)
    with pytest.raises(OSError, match="disk full"):
        restore_backup(archive, database_url=url)

    # The live DB is untouched — staging happens before displacement.
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT v FROM t").fetchone()[0] == "original"
    conn.close()
    assert not (config_dir / "shoreguard.db.pre-restore").exists()
    assert _shutil.which is not None  # keep the import honest


def test_restore_leaves_no_staging_files(config_dir, db_file) -> None:
    url = f"sqlite:///{db_file}"
    archive = create_backup(database_url=url)
    restore_backup(archive, database_url=url)
    assert not list(config_dir.glob("*.restore-tmp"))
