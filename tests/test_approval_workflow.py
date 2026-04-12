"""Tests for M19 multi-stage approvals (quorum) — service + API."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import Base
from shoreguard.services.approval_workflow import ApprovalWorkflowService

GW = "test"
SB = "sb1"
CHUNK = "chunk-1"
WF_URL = f"/api/gateways/{GW}/sandboxes/{SB}/approval-workflow"
APPROVE_URL = f"/api/gateways/{GW}/sandboxes/{SB}/approvals/{CHUNK}/approve"
REJECT_URL = f"/api/gateways/{GW}/sandboxes/{SB}/approvals/{CHUNK}/reject"
APPROVE_ALL_URL = f"/api/gateways/{GW}/sandboxes/{SB}/approvals/approve-all"
DECISIONS_URL = f"/api/gateways/{GW}/sandboxes/{SB}/approvals/{CHUNK}/decisions"


@pytest.fixture
def wf_svc():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    svc = ApprovalWorkflowService(factory)
    yield svc
    engine.dispose()


def _mock_approve(mock_client):
    mock_client.approvals.approve.return_value = {
        "id": CHUNK,
        "status": "approved",
        "policy_version": 2,
    }


def _mock_reject(mock_client):
    mock_client.approvals.reject.return_value = None


# ─── Service: CRUD ──────────────────────────────────────────────────


class TestWorkflowCRUD:
    def test_upsert_creates(self, wf_svc):
        wf = wf_svc.upsert_workflow(
            GW,
            SB,
            required_approvals=2,
            required_roles=[],
            distinct_actors=True,
            escalation_timeout_minutes=None,
            actor="admin",
        )
        assert wf["required_approvals"] == 2
        assert wf["distinct_actors"] is True
        assert wf["required_roles"] == []

    def test_upsert_replaces(self, wf_svc):
        wf_svc.upsert_workflow(
            GW,
            SB,
            required_approvals=2,
            required_roles=[],
            distinct_actors=True,
            escalation_timeout_minutes=None,
            actor="admin",
        )
        wf = wf_svc.upsert_workflow(
            GW,
            SB,
            required_approvals=3,
            required_roles=["admin"],
            distinct_actors=False,
            escalation_timeout_minutes=15,
            actor="admin2",
        )
        assert wf["required_approvals"] == 3
        assert wf["required_roles"] == ["admin"]
        assert wf["distinct_actors"] is False
        assert wf["escalation_timeout_minutes"] == 15

    def test_upsert_rejects_zero(self, wf_svc):
        with pytest.raises(ValueError):
            wf_svc.upsert_workflow(
                GW,
                SB,
                required_approvals=0,
                required_roles=[],
                distinct_actors=True,
                escalation_timeout_minutes=None,
                actor="admin",
            )

    def test_get_none(self, wf_svc):
        assert wf_svc.get_workflow(GW, SB) is None

    def test_delete_existing(self, wf_svc):
        wf_svc.upsert_workflow(
            GW,
            SB,
            required_approvals=2,
            required_roles=[],
            distinct_actors=True,
            escalation_timeout_minutes=None,
            actor="admin",
        )
        assert wf_svc.delete_workflow(GW, SB) is True
        assert wf_svc.get_workflow(GW, SB) is None

    def test_delete_nonexistent(self, wf_svc):
        assert wf_svc.delete_workflow(GW, SB) is False


# ─── Service: Voting ────────────────────────────────────────────────


def _make_workflow(
    svc: ApprovalWorkflowService,
    required: int = 2,
    roles: list[str] | None = None,
    distinct: bool = True,
    escalate: int | None = None,
) -> None:
    svc.upsert_workflow(
        GW,
        SB,
        required_approvals=required,
        required_roles=roles or [],
        distinct_actors=distinct,
        escalation_timeout_minutes=escalate,
        actor="admin",
    )


class TestRecordDecision:
    def test_first_vote_pending(self, wf_svc):
        _make_workflow(wf_svc, required=2)
        result = wf_svc.record_decision(
            GW, SB, CHUNK, actor="alice", role="operator", decision="approve"
        )
        assert result.quorum_met is False
        assert result.reject_seen is False
        assert len(result.decisions) == 1
        assert result.votes_needed == 2

    def test_second_vote_meets_quorum(self, wf_svc):
        _make_workflow(wf_svc, required=2)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")
        result = wf_svc.record_decision(
            GW, SB, CHUNK, actor="bob", role="operator", decision="approve"
        )
        assert result.quorum_met is True
        assert result.reject_seen is False
        # Rows cleared after terminal state
        assert wf_svc.list_decisions(GW, SB, CHUNK) == []

    def test_single_reject_kills(self, wf_svc):
        _make_workflow(wf_svc, required=3)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")
        result = wf_svc.record_decision(
            GW, SB, CHUNK, actor="bob", role="operator", decision="reject"
        )
        assert result.reject_seen is True
        assert result.quorum_met is False
        assert wf_svc.list_decisions(GW, SB, CHUNK) == []

    def test_distinct_actors_rejects_duplicate(self, wf_svc):
        _make_workflow(wf_svc, required=2, distinct=True)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")
        with pytest.raises(ValueError, match="already voted"):
            wf_svc.record_decision(
                GW, SB, CHUNK, actor="alice", role="operator", decision="approve"
            )

    def test_non_distinct_allows_same_actor(self, wf_svc):
        _make_workflow(wf_svc, required=2, distinct=False)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")
        result = wf_svc.record_decision(
            GW, SB, CHUNK, actor="alice", role="operator", decision="approve"
        )
        assert result.quorum_met is True

    def test_role_whitelist_enforced(self, wf_svc):
        _make_workflow(wf_svc, required=2, roles=["admin"])
        with pytest.raises(PermissionError):
            wf_svc.record_decision(
                GW, SB, CHUNK, actor="alice", role="operator", decision="approve"
            )

    def test_role_whitelist_allows_listed(self, wf_svc):
        _make_workflow(wf_svc, required=1, roles=["admin"])
        result = wf_svc.record_decision(
            GW, SB, CHUNK, actor="alice", role="admin", decision="approve"
        )
        assert result.quorum_met is True

    def test_invalid_decision_rejected(self, wf_svc):
        _make_workflow(wf_svc, required=2)
        with pytest.raises(ValueError):
            wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="maybe")

    def test_record_without_workflow_fails(self, wf_svc):
        with pytest.raises(LookupError):
            wf_svc.record_decision(
                GW, SB, CHUNK, actor="alice", role="operator", decision="approve"
            )

    def test_list_decisions_empty(self, wf_svc):
        _make_workflow(wf_svc, required=2)
        assert wf_svc.list_decisions(GW, SB, CHUNK) == []

    def test_list_decisions_sorted(self, wf_svc):
        _make_workflow(wf_svc, required=5)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")
        wf_svc.record_decision(GW, SB, CHUNK, actor="bob", role="operator", decision="approve")
        listed = wf_svc.list_decisions(GW, SB, CHUNK)
        assert [d["actor"] for d in listed] == ["alice", "bob"]

    def test_has_pending_false_after_clear(self, wf_svc):
        _make_workflow(wf_svc, required=1)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")
        # quorum met → rows cleared
        assert wf_svc.has_pending(GW, SB) is False

    def test_has_pending_true_mid_flight(self, wf_svc):
        _make_workflow(wf_svc, required=3)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")
        assert wf_svc.has_pending(GW, SB) is True


class TestCheckQuorum:
    def test_no_decisions(self):
        assert ApprovalWorkflowService.check_quorum({"required_approvals": 2}, []) is False

    def test_exact(self):
        assert (
            ApprovalWorkflowService.check_quorum(
                {"required_approvals": 2},
                [
                    {"decision": "approve"},
                    {"decision": "approve"},
                ],
            )
            is True
        )

    def test_reject_blocks(self):
        assert (
            ApprovalWorkflowService.check_quorum(
                {"required_approvals": 1},
                [{"decision": "approve"}, {"decision": "reject"}],
            )
            is False
        )

    def test_more_than_enough(self):
        assert (
            ApprovalWorkflowService.check_quorum(
                {"required_approvals": 2},
                [
                    {"decision": "approve"},
                    {"decision": "approve"},
                    {"decision": "approve"},
                ],
            )
            is True
        )


class TestEscalation:
    def test_escalation_triggers_after_timeout(self, wf_svc):
        _make_workflow(wf_svc, required=3, escalate=1)
        wf_svc.record_decision(GW, SB, CHUNK, actor="alice", role="operator", decision="approve")

        # Backdate the first decision by 2 minutes
        from shoreguard.models import ApprovalDecision

        with wf_svc._session_factory() as session:
            row = session.query(ApprovalDecision).filter_by(chunk_id=CHUNK, actor="alice").one()
            row.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=2)
            session.commit()

        result = wf_svc.record_decision(
            GW, SB, CHUNK, actor="bob", role="operator", decision="approve"
        )
        assert result.escalated is True

    def test_no_escalation_without_timeout(self, wf_svc):
        _make_workflow(wf_svc, required=3, escalate=None)
        result = wf_svc.record_decision(
            GW, SB, CHUNK, actor="alice", role="operator", decision="approve"
        )
        assert result.escalated is False


# ─── API integration ─────────────────────────────────────────────────


@pytest.fixture
def _policy_loaded(mock_client):
    mock_client.policies.get.return_value = {
        "active_version": 2,
        "revision": {"version": 2, "status": "loaded"},
    }


class TestWorkflowRoutes:
    async def test_get_empty(self, api_client):
        resp = await api_client.get(WF_URL)
        assert resp.status_code == 200
        assert resp.json() == {}

    async def test_put_creates_workflow(self, api_client):
        resp = await api_client.put(
            WF_URL,
            json={
                "required_approvals": 2,
                "required_roles": ["admin", "operator"],
                "distinct_actors": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["required_approvals"] == 2
        assert data["required_roles"] == ["admin", "operator"]

    async def test_put_rejects_zero_approvals(self, api_client):
        resp = await api_client.put(
            WF_URL,
            json={"required_approvals": 0, "required_roles": [], "distinct_actors": True},
        )
        assert resp.status_code == 422  # pydantic validation

    async def test_delete_404_when_absent(self, api_client):
        resp = await api_client.delete(WF_URL)
        assert resp.status_code == 404

    async def test_delete_removes(self, api_client):
        await api_client.put(
            WF_URL,
            json={"required_approvals": 2, "required_roles": [], "distinct_actors": True},
        )
        resp = await api_client.delete(WF_URL)
        assert resp.status_code == 200
        assert (await api_client.get(WF_URL)).json() == {}

    async def test_get_decisions_empty(self, api_client):
        await api_client.put(
            WF_URL,
            json={"required_approvals": 2, "required_roles": [], "distinct_actors": True},
        )
        resp = await api_client.get(DECISIONS_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["votes"] == 0
        assert data["needed"] == 2
        assert data["decisions"] == []


class TestApproveFastPath:
    """Without a workflow, approve calls upstream directly."""

    async def test_approve_fast_path(self, api_client, mock_client):
        _mock_approve(mock_client)
        resp = await api_client.post(APPROVE_URL)
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        mock_client.approvals.approve.assert_called_once()


class TestApproveQuorumPath:
    """With a workflow, approve records a vote and only fires upstream on quorum."""

    async def test_first_vote_returns_202(self, api_client, mock_client):
        await api_client.put(
            WF_URL,
            json={
                "required_approvals": 2,
                "required_roles": [],
                "distinct_actors": False,
            },
        )
        _mock_approve(mock_client)
        resp = await api_client.post(APPROVE_URL)
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "pending"
        assert data["votes"] == 1
        assert data["needed"] == 2
        mock_client.approvals.approve.assert_not_called()

    async def test_quorum_met_fires_upstream(self, api_client, mock_client):
        # distinct_actors=False so the same no-auth user can vote twice
        await api_client.put(
            WF_URL,
            json={
                "required_approvals": 2,
                "required_roles": [],
                "distinct_actors": False,
            },
        )
        _mock_approve(mock_client)
        r1 = await api_client.post(APPROVE_URL)
        assert r1.status_code == 202
        r2 = await api_client.post(APPROVE_URL)
        assert r2.status_code == 200
        assert r2.json()["status"] == "approved"
        mock_client.approvals.approve.assert_called_once()

    async def test_duplicate_distinct_actor_409(self, api_client, mock_client):
        await api_client.put(
            WF_URL,
            json={
                "required_approvals": 2,
                "required_roles": [],
                "distinct_actors": True,
            },
        )
        _mock_approve(mock_client)
        r1 = await api_client.post(APPROVE_URL)
        assert r1.status_code == 202
        r2 = await api_client.post(APPROVE_URL)
        assert r2.status_code == 409

    async def test_reject_under_workflow_fires_upstream(self, api_client, mock_client):
        await api_client.put(
            WF_URL,
            json={"required_approvals": 3, "required_roles": [], "distinct_actors": True},
        )
        _mock_reject(mock_client)
        resp = await api_client.post(REJECT_URL)
        assert resp.status_code == 200
        mock_client.approvals.reject.assert_called_once()


class TestApproveAllUnderWorkflow:
    async def test_admin_override_allowed(self, api_client, mock_client):
        # no_auth → admin role, so admin override should pass
        await api_client.put(
            WF_URL,
            json={"required_approvals": 2, "required_roles": [], "distinct_actors": True},
        )
        mock_client.approvals.approve_all.return_value = {"approved": 3}
        resp = await api_client.post(APPROVE_ALL_URL, json={"include_security_flagged": False})
        assert resp.status_code == 200
        mock_client.approvals.approve_all.assert_called_once()
