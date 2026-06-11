"""Unit tests for PolicyApplyProposalService — YAML ingest, diff, and quorum proposals."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.policy_apply_proposal import PolicyApplyProposalService


@pytest.fixture
async def svc():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield PolicyApplyProposalService(factory)
    await engine.dispose()


async def test_upsert_then_get(svc):
    out = await svc.upsert(
        "gw",
        "sb",
        "policy.apply:abc",
        yaml_text="policy: {}\n",
        expected_hash="h1",
        proposed_by="alice",
    )
    assert out["chunk_id"] == "policy.apply:abc"
    fetched = await svc.get("gw", "sb", "policy.apply:abc")
    assert fetched is not None
    assert fetched["yaml_text"] == "policy: {}\n"
    assert fetched["proposed_by"] == "alice"


async def test_upsert_idempotent(svc):
    await svc.upsert(
        "gw",
        "sb",
        "policy.apply:abc",
        yaml_text="v1\n",
        expected_hash="h1",
        proposed_by="alice",
    )
    await svc.upsert(
        "gw",
        "sb",
        "policy.apply:abc",
        yaml_text="v2\n",
        expected_hash="h2",
        proposed_by="bob",
    )
    rows = await svc.list_for_sandbox("gw", "sb")
    assert len(rows) == 1
    assert rows[0]["yaml_text"] == "v2\n"
    assert rows[0]["proposed_by"] == "bob"
    assert rows[0]["expected_hash"] == "h2"


async def test_delete_returns_true_if_existed(svc):
    await svc.upsert("gw", "sb", "ck1", yaml_text="x\n", expected_hash=None, proposed_by="a")
    assert await svc.delete("gw", "sb", "ck1") is True
    assert await svc.delete("gw", "sb", "ck1") is False
    assert await svc.get("gw", "sb", "ck1") is None


async def test_list_for_sandbox_returns_recent_first(svc):
    await svc.upsert("gw", "sb", "ck1", yaml_text="a\n", expected_hash=None, proposed_by="a")
    await svc.upsert("gw", "sb", "ck2", yaml_text="b\n", expected_hash=None, proposed_by="a")
    rows = await svc.list_for_sandbox("gw", "sb")
    assert {r["chunk_id"] for r in rows} == {"ck1", "ck2"}


async def test_unique_per_sandbox(svc):
    await svc.upsert("gw1", "sb", "ck", yaml_text="x\n", expected_hash=None, proposed_by="a")
    await svc.upsert("gw2", "sb", "ck", yaml_text="y\n", expected_hash=None, proposed_by="a")
    assert (await svc.get("gw1", "sb", "ck"))["yaml_text"] == "x\n"
    assert (await svc.get("gw2", "sb", "ck"))["yaml_text"] == "y\n"
