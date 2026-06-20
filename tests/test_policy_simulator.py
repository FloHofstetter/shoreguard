"""Tests for the policy simulator: narrowness gate, denial store, replay."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.denial_store import DenialSampleStore
from shoreguard.services.policy_simulator import annotate_narrowness, assess_chunk
from shoreguard.services.prover import ProverService

# ── Slice A: narrowness gate ────────────────────────────────────────────────


def test_narrowness_over_broad_host() -> None:
    chunk = {"proposed_rule": {"endpoints": [{"host": "**", "port": 443}]}}
    verdict = assess_chunk(chunk)
    assert verdict["verdict"] == "over_broad"
    assert any("host" in f for f in verdict["over_broad_fields"])


def test_narrowness_narrow_exact_host() -> None:
    chunk = {"proposed_rule": {"endpoints": [{"host": "api.example.com", "port": 443}]}}
    assert assess_chunk(chunk)["verdict"] == "narrow"
    # A scoped wildcard suffix is not flagged.
    chunk2 = {"proposed_rule": {"endpoints": [{"host": "*.example.com", "port": 443}]}}
    assert assess_chunk(chunk2)["verdict"] == "narrow"


def test_narrowness_over_broad_path() -> None:
    chunk = {
        "proposed_rule": {
            "endpoints": [{"host": "api.example.com", "rules": [{"allow": {"path": "/**"}}]}]
        }
    }
    assert assess_chunk(chunk)["verdict"] == "over_broad"


def test_narrowness_unknown_without_rule() -> None:
    assert assess_chunk({})["verdict"] == "unknown"
    assert assess_chunk({"proposed_rule": {"endpoints": []}})["verdict"] == "unknown"


def test_annotate_narrowness_sets_field() -> None:
    chunks = [{"proposed_rule": {"endpoints": [{"host": "**"}]}}, {"proposed_rule": {}}]
    annotate_narrowness(chunks)
    assert chunks[0]["narrowness"]["verdict"] == "over_broad"
    assert chunks[1]["narrowness"]["verdict"] == "unknown"


# ── Slice B: denial store ───────────────────────────────────────────────────


@pytest.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_denial_store_persist_and_list(factory) -> None:
    store = DenialSampleStore(factory)
    summaries = [
        {
            "binary": "/usr/bin/curl",
            "host": "API.Example.com.",
            "port": 443,
            "deny_reason": "host not allowed",
            "count": 3,
            "l7_request_samples": [{"method": "GET", "path": "/v1/data"}],
        }
    ]
    assert await store.persist_summaries("gw1", "agent-a", summaries) == 1
    # Re-persisting the same key upserts (still one row).
    assert await store.persist_summaries("gw1", "agent-a", summaries) == 1
    rows = await store.list_for_sandbox("gw1", "agent-a")
    assert len(rows) == 1
    assert rows[0]["host"] == "api.example.com"  # normalized
    assert rows[0]["l7"] == [{"method": "GET", "path": "/v1/data"}]
    # Summaries without a host are skipped.
    assert await store.persist_summaries("gw1", "agent-a", [{"binary": "x"}]) == 0


async def test_denial_store_prune(factory) -> None:
    store = DenialSampleStore(factory)
    await store.persist_summaries(
        "gw1", "agent-a", [{"binary": "b", "host": "h", "port": 1, "l7_request_samples": []}]
    )
    await store.prune(retention_days=0)  # cutoff = now → drops the just-written row
    # Allow a tick of skew: prune with a negative-effect retention removes it.
    rows = await store.list_for_sandbox("gw1", "agent-a")
    assert rows == []


# ── Slice B: replay ─────────────────────────────────────────────────────────


def test_replay_predicts_allow_and_deny() -> None:
    prover = ProverService(timeout_ms=2000)
    # Policy allows GET to api.example.com:443 only.
    policy = {
        "network_policies": {
            "rule1": {
                "endpoints": [
                    {
                        "host": "api.example.com",
                        "port": 443,
                        "rules": [{"allow": {"methods": ["GET"], "path": "/**"}}],
                    }
                ]
            }
        }
    }
    requests = [
        {"binary": "curl", "host": "api.example.com", "port": 443, "method": "GET", "path": "/v1"},
        {"binary": "curl", "host": "evil.example.com", "port": 443, "method": "GET", "path": "/v1"},
    ]
    results = prover.replay_denials(policy, requests)
    decisions = {r["host"]: r["predicted_decision"] for r in results}
    assert decisions["api.example.com"] == "allow"
    assert decisions["evil.example.com"] == "deny"
