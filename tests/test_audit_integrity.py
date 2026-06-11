"""Tests for append-only enforcement and offline audit export."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import AuditEntry, Base
from shoreguard.services.audit import AuditIntegrityError, AuditService


@pytest.fixture
async def audit_svc():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = AuditService(factory)
    yield svc
    await engine.dispose()


async def _insert_entry(svc: AuditService) -> None:
    await svc.log(
        actor="admin@test.com",
        actor_role="admin",
        action="sandbox.create",
        resource_type="sandbox",
        resource_id="sb-1",
    )


async def test_update_is_blocked(audit_svc: AuditService) -> None:
    await _insert_entry(audit_svc)
    factory = audit_svc._session_factory
    async with factory() as session:
        entry = (await session.execute(select(AuditEntry))).scalars().first()
        assert entry is not None
        entry.actor = "tampered@evil.com"
        with pytest.raises(AuditIntegrityError, match="UPDATE is not allowed"):
            await session.commit()


async def test_delete_outside_cleanup_is_blocked(audit_svc: AuditService) -> None:
    await _insert_entry(audit_svc)
    factory = audit_svc._session_factory
    async with factory() as session:
        entry = (await session.execute(select(AuditEntry))).scalars().first()
        assert entry is not None
        await session.delete(entry)
        with pytest.raises(AuditIntegrityError, match="DELETE only allowed"):
            await session.commit()


async def test_cleanup_can_delete(audit_svc: AuditService) -> None:
    # Insert an old entry directly so cleanup's threshold matches
    factory = audit_svc._session_factory
    async with factory() as session:
        session.add(
            AuditEntry(
                timestamp=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
                actor="old@test.com",
                actor_role="admin",
                action="x",
                resource_type="y",
            )
        )
        await session.commit()
    removed = await audit_svc.cleanup(older_than_days=1)
    assert removed == 1
    assert await audit_svc.list() == []


async def test_cleanup_bypass_is_scoped(audit_svc: AuditService) -> None:
    """After cleanup() returns, deletion must be blocked again."""
    await _insert_entry(audit_svc)
    # cleanup with no old rows — should return 0 without raising
    await audit_svc.cleanup(older_than_days=3650)
    # Now try a normal delete — must still raise
    factory = audit_svc._session_factory
    async with factory() as session:
        entry = (await session.execute(select(AuditEntry))).scalars().first()
        await session.delete(entry)
        with pytest.raises(AuditIntegrityError):
            await session.commit()


async def test_export_json_serializes_entries(audit_svc: AuditService) -> None:
    await _insert_entry(audit_svc)
    out = await audit_svc.export_json()
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["actor"] == "admin@test.com"
    assert data[0]["action"] == "sandbox.create"


def test_audit_export_cli_writes_three_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shoreguard audit export`` writes export, sha256 digest, and manifest."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from shoreguard.models import Base
    from shoreguard.services.audit import AuditService

    # Seed a file-backed DB the CLI's own event loop can reopen.
    db_path = tmp_path / "audit.db"

    async def _seed() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        svc = AuditService(async_sessionmaker(engine, expire_on_commit=False))
        await _insert_entry(svc)
        await engine.dispose()

    asyncio.run(_seed())

    import shoreguard.db as _db

    sync_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(_db, "get_engine", lambda: sync_engine)
    monkeypatch.setattr(_db, "init_db", lambda *_a, **_kw: sync_engine)

    from typer.testing import CliRunner

    from shoreguard.cli import cli

    out_file = tmp_path / "audit.json"
    result = CliRunner().invoke(
        cli,
        ["audit", "export", "--out", str(out_file), "--format", "json"],
    )
    assert result.exit_code == 0, result.output

    sha_file = out_file.with_name("audit.json.sha256")
    manifest_file = out_file.with_name("audit.json.manifest.json")

    assert out_file.exists()
    assert sha_file.exists()
    assert manifest_file.exists()

    # SHA256 digest matches the actual file
    expected_digest = hashlib.sha256(out_file.read_bytes()).hexdigest()
    sha_content = sha_file.read_text().strip()
    assert sha_content.startswith(expected_digest)
    assert sha_content.endswith("audit.json")

    # Manifest references the correct file and entry count
    manifest = json.loads(manifest_file.read_text())
    assert manifest["file"] == "audit.json"
    assert manifest["sha256"] == expected_digest
    assert manifest["entries"] == 1
    assert manifest["format"] == "json"

    # File permissions are 0600
    assert oct(out_file.stat().st_mode)[-3:] == "600"
    assert oct(sha_file.stat().st_mode)[-3:] == "600"
    assert oct(manifest_file.stat().st_mode)[-3:] == "600"
