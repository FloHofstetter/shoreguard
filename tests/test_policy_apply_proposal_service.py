"""Unit tests for PolicyApplyProposalService (M23)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.policy_apply_proposal import PolicyApplyProposalService


@pytest.fixture
def svc():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield PolicyApplyProposalService(factory)
    engine.dispose()


def test_upsert_then_get(svc):
    out = svc.upsert(
        "gw",
        "sb",
        "policy.apply:abc",
        yaml_text="policy: {}\n",
        expected_hash="h1",
        proposed_by="alice",
    )
    assert out["chunk_id"] == "policy.apply:abc"
    fetched = svc.get("gw", "sb", "policy.apply:abc")
    assert fetched is not None
    assert fetched["yaml_text"] == "policy: {}\n"
    assert fetched["proposed_by"] == "alice"


def test_upsert_idempotent(svc):
    svc.upsert(
        "gw",
        "sb",
        "policy.apply:abc",
        yaml_text="v1\n",
        expected_hash="h1",
        proposed_by="alice",
    )
    svc.upsert(
        "gw",
        "sb",
        "policy.apply:abc",
        yaml_text="v2\n",
        expected_hash="h2",
        proposed_by="bob",
    )
    rows = svc.list_for_sandbox("gw", "sb")
    assert len(rows) == 1
    assert rows[0]["yaml_text"] == "v2\n"
    assert rows[0]["proposed_by"] == "bob"
    assert rows[0]["expected_hash"] == "h2"


def test_delete_returns_true_if_existed(svc):
    svc.upsert("gw", "sb", "ck1", yaml_text="x\n", expected_hash=None, proposed_by="a")
    assert svc.delete("gw", "sb", "ck1") is True
    assert svc.delete("gw", "sb", "ck1") is False
    assert svc.get("gw", "sb", "ck1") is None


def test_list_for_sandbox_returns_recent_first(svc):
    svc.upsert("gw", "sb", "ck1", yaml_text="a\n", expected_hash=None, proposed_by="a")
    svc.upsert("gw", "sb", "ck2", yaml_text="b\n", expected_hash=None, proposed_by="a")
    rows = svc.list_for_sandbox("gw", "sb")
    assert {r["chunk_id"] for r in rows} == {"ck1", "ck2"}


def test_unique_per_sandbox(svc):
    svc.upsert("gw1", "sb", "ck", yaml_text="x\n", expected_hash=None, proposed_by="a")
    svc.upsert("gw2", "sb", "ck", yaml_text="y\n", expected_hash=None, proposed_by="a")
    assert svc.get("gw1", "sb", "ck")["yaml_text"] == "x\n"
    assert svc.get("gw2", "sb", "ck")["yaml_text"] == "y\n"
