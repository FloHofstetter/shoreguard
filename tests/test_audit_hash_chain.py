"""Tests for the tamper-evident audit hash chain."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.audit import AuditService


@pytest.fixture
async def setup():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield AuditService(factory), factory
    await engine.dispose()


async def _write(svc: AuditService, n: int = 3) -> None:
    for i in range(n):
        await svc.log(
            actor="admin@test.com",
            actor_role="admin",
            action=f"thing.{i}",
            resource_type="thing",
            resource_id=str(i),
            detail={"i": i},
        )


async def test_chain_links_consecutive_entries(setup) -> None:
    svc, factory = setup
    await _write(svc, 3)

    async with factory() as session:
        rows = (
            await session.execute(
                text("SELECT id, prev_hash, entry_hash FROM audit_log ORDER BY id")
            )
        ).all()
    assert len(rows) == 3
    assert rows[0].prev_hash is None  # chain start
    assert rows[0].entry_hash is not None
    assert rows[1].prev_hash == rows[0].entry_hash
    assert rows[2].prev_hash == rows[1].entry_hash


async def test_verify_intact_chain(setup) -> None:
    svc, _ = setup
    await _write(svc, 5)
    result = await svc.verify_chain()
    assert result == {"ok": True, "checked": 5, "legacy": 0, "first_bad_id": None}


async def test_verify_detects_field_tampering(setup) -> None:
    svc, factory = setup
    await _write(svc, 3)

    async with factory() as session:
        await session.execute(
            text("UPDATE audit_log SET actor = 'evil@test.com' WHERE id = 2")
        )
        await session.commit()

    result = await svc.verify_chain()
    assert result["ok"] is False
    assert result["first_bad_id"] == 2


async def test_verify_detects_deleted_middle_row(setup) -> None:
    svc, factory = setup
    await _write(svc, 3)

    async with factory() as session:
        await session.execute(text("DELETE FROM audit_log WHERE id = 2"))
        await session.commit()

    result = await svc.verify_chain()
    assert result["ok"] is False
    assert result["first_bad_id"] == 3  # row 3's back-link no longer matches


async def test_verify_skips_legacy_prefix(setup) -> None:
    svc, factory = setup
    # Simulate pre-chain rows (NULL hashes) followed by chained writes.
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO audit_log (timestamp, actor, actor_role, action,"
                " resource_type, resource_id) VALUES ('2026-01-01 00:00:00',"
                " 'old@test.com', 'admin', 'legacy.action', 'thing', '0')"
            )
        )
        await session.commit()
    await _write(svc, 2)

    result = await svc.verify_chain()
    assert result["ok"] is True
    assert result["legacy"] == 1
    assert result["checked"] == 2


async def test_verify_flags_unhashed_row_after_chain(setup) -> None:
    svc, factory = setup
    await _write(svc, 2)
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO audit_log (timestamp, actor, actor_role, action,"
                " resource_type, resource_id) VALUES ('2026-06-11 00:00:00',"
                " 'evil@test.com', 'admin', 'sneaky.insert', 'thing', '0')"
            )
        )
        await session.commit()

    result = await svc.verify_chain()
    assert result["ok"] is False
    assert result["first_bad_id"] == 3


async def test_verify_empty_log(setup) -> None:
    svc, _ = setup
    result = await svc.verify_chain()
    assert result == {"ok": True, "checked": 0, "legacy": 0, "first_bad_id": None}
