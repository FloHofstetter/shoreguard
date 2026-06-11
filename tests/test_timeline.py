"""Tests for the per-sandbox activity timeline."""

from __future__ import annotations

import datetime
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import (
    ApprovalDecision,
    ApprovalWorkflow,
    AuditEntry,
    Base,
    KillSwitchEntry,
    SandboxUsage,
)
from shoreguard.services.timeline import TimelineService

NOW = datetime.datetime.now(datetime.UTC)


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
    yield TimelineService(factory), factory
    await engine.dispose()


async def _seed(factory) -> None:
    async with factory() as session:
        session.add(
            AuditEntry(
                timestamp=NOW - datetime.timedelta(hours=1),
                actor="admin@test.com",
                actor_role="admin",
                action="policy.preset.apply",
                resource_type="policy",
                resource_id="agent-a",
                gateway_name="gw1",
                detail=json.dumps({"preset": "github"}),
            )
        )
        workflow = ApprovalWorkflow(
            gateway_name="gw1",
            sandbox_name="agent-a",
            required_approvals=2,
            created_by="admin@test.com",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(workflow)
        await session.flush()
        session.add(
            ApprovalDecision(
                workflow_id=workflow.id,
                gateway_name="gw1",
                sandbox_name="agent-a",
                chunk_id="chunk-7",
                actor="admin@test.com",
                role="admin",
                decision="approve",
                comment="looks safe",
                created_at=NOW - datetime.timedelta(hours=2),
            )
        )
        session.add(
            KillSwitchEntry(
                gateway="gw1",
                sandbox="agent-a",
                providers_json=json.dumps(["anthropic", "github"]),
                engaged_at=NOW - datetime.timedelta(hours=3),
                engaged_by="curfew",
            )
        )
        session.add(
            SandboxUsage(
                gateway="gw1",
                sandbox="agent-a",
                day=NOW.date().isoformat(),
                requests=42,
            )
        )
        # Noise that must NOT appear: other sandbox / other gateway.
        session.add(
            AuditEntry(
                timestamp=NOW - datetime.timedelta(hours=1),
                actor="admin@test.com",
                actor_role="admin",
                action="other.action",
                resource_type="sandbox",
                resource_id="agent-b",
                gateway_name="gw1",
            )
        )
        session.add(
            SandboxUsage(gateway="gw2", sandbox="agent-a", day=NOW.date().isoformat(), requests=9)
        )
        await session.commit()


async def test_timeline_merges_all_sources_newest_first(setup) -> None:
    svc, factory = setup
    await _seed(factory)

    items = await svc.for_sandbox("gw1", "agent-a", hours=24)

    kinds = [i["kind"] for i in items]
    assert set(kinds) == {"audit", "approval", "kill_switch", "usage"}
    timestamps = [i["ts"] for i in items]
    assert timestamps == sorted(timestamps, reverse=True)
    audit_item = next(i for i in items if i["kind"] == "audit")
    assert audit_item["title"] == "policy.preset.apply"
    assert "preset=github" in audit_item["detail"]
    ks_item = next(i for i in items if i["kind"] == "kill_switch")
    assert "2 provider(s) cut by curfew" in ks_item["detail"]


async def test_timeline_filters_other_sandboxes_and_gateways(setup) -> None:
    svc, factory = setup
    await _seed(factory)

    items = await svc.for_sandbox("gw1", "agent-a", hours=24)

    assert all("agent-b" not in i["detail"] for i in items)
    usage_items = [i for i in items if i["kind"] == "usage"]
    assert len(usage_items) == 1
    assert "42" in usage_items[0]["title"]


async def test_timeline_window_excludes_old_events(setup) -> None:
    svc, factory = setup
    async with factory() as session:
        session.add(
            AuditEntry(
                timestamp=NOW - datetime.timedelta(days=10),
                actor="x",
                actor_role="admin",
                action="ancient.event",
                resource_type="sandbox",
                resource_id="agent-a",
                gateway_name="gw1",
            )
        )
        await session.commit()

    items = await svc.for_sandbox("gw1", "agent-a", hours=24)
    assert items == []

    items = await svc.for_sandbox("gw1", "agent-a", hours=24 * 14)
    assert len(items) == 1
    assert items[0]["title"] == "ancient.event"


async def test_timeline_respects_limit(setup) -> None:
    svc, factory = setup
    async with factory() as session:
        for i in range(10):
            session.add(
                AuditEntry(
                    timestamp=NOW - datetime.timedelta(minutes=i),
                    actor="x",
                    actor_role="admin",
                    action=f"event.{i}",
                    resource_type="sandbox",
                    resource_id="agent-a",
                    gateway_name="gw1",
                )
            )
        await session.commit()

    items = await svc.for_sandbox("gw1", "agent-a", hours=24, limit=5)
    assert len(items) == 5
    assert items[0]["title"] == "event.0"  # newest first


async def test_timeline_empty_sandbox(setup) -> None:
    svc, _ = setup
    assert await svc.for_sandbox("gw1", "nope", hours=24) == []
