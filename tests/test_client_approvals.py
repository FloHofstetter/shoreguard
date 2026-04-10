"""Unit tests for ApprovalManager — FakeStub pattern."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shoreguard.client._proto import openshell_pb2, sandbox_pb2
from shoreguard.client.approvals import ApprovalManager, _chunk_to_dict


class _FakeStub:
    def __init__(self) -> None:
        self.request = None

    def GetDraftPolicy(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            chunks=[openshell_pb2.PolicyChunk(id="chunk-1", rule_name="pypi", status="pending")],
            rolling_summary="summary",
            draft_version=2,
            last_analyzed_at_ms=1000,
        )

    def ApproveDraftChunk(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(policy_version=5, policy_hash="abc")

    def RejectDraftChunk(self, req, timeout=None):
        self.request = req
        return SimpleNamespace()

    def ApproveAllDraftChunks(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            policy_version=5, policy_hash="abc", chunks_approved=3, chunks_skipped=1
        )

    def EditDraftChunk(self, req, timeout=None):
        self.request = req
        return SimpleNamespace()

    def UndoDraftChunk(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(policy_version=4, policy_hash="old")

    def ClearDraftChunks(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(chunks_cleared=5)

    def GetDraftHistory(self, req, timeout=None):
        self.request = req
        return SimpleNamespace(
            entries=[
                SimpleNamespace(
                    timestamp_ms=1000,
                    event_type="approved",
                    description="ok",
                    chunk_id="chunk-1",
                ),
            ]
        )


@pytest.fixture
def stub():
    return _FakeStub()


@pytest.fixture
def mgr(stub):
    m = object.__new__(ApprovalManager)
    m._stub = stub
    m._timeout = 30.0
    return m


def test_get_draft_sends_sandbox_name(mgr, stub):
    """get_draft() sends sandbox name and returns chunks list."""
    result = mgr.get_draft("sb1")

    assert stub.request.name == "sb1"
    assert result["draft_version"] == 2
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["rule_name"] == "pypi"


def test_approve_sends_chunk_id(mgr, stub):
    """approve() sends sandbox name + chunk_id and returns policy version."""
    result = mgr.approve("sb1", "chunk-1")

    assert stub.request.name == "sb1"
    assert stub.request.chunk_id == "chunk-1"
    assert result["policy_version"] == 5


def test_reject_sends_reason(mgr, stub):
    """reject() sends reason in request."""
    mgr.reject("sb1", "chunk-1", reason="too permissive")

    assert stub.request.name == "sb1"
    assert stub.request.chunk_id == "chunk-1"
    assert stub.request.reason == "too permissive"


def test_approve_all_sends_flag(mgr, stub):
    """approve_all() sends include_security_flagged and returns counts."""
    result = mgr.approve_all("sb1", include_security_flagged=True)

    assert stub.request.include_security_flagged is True
    assert result["chunks_approved"] == 3
    assert result["chunks_skipped"] == 1


def test_edit_converts_proposed_rule(mgr, stub):
    """edit() converts dict to NetworkPolicyRule proto via _dict_to_network_rule."""
    mgr.edit("sb1", "chunk-1", {"name": "pypi", "endpoints": [], "binaries": []})

    assert stub.request.name == "sb1"
    assert stub.request.chunk_id == "chunk-1"
    # proposed_rule is a real NetworkPolicyRule proto
    assert stub.request.proposed_rule.name == "pypi"


def test_undo_sends_chunk_id(mgr, stub):
    """undo() sends chunk_id and returns policy version."""
    result = mgr.undo("sb1", "chunk-1")

    assert stub.request.chunk_id == "chunk-1"
    assert result["policy_version"] == 4


def test_clear_returns_count(mgr, stub):
    """clear() returns chunks_cleared count."""
    result = mgr.clear("sb1")

    assert stub.request.name == "sb1"
    assert result["chunks_cleared"] == 5


def test_get_history_returns_list(mgr, stub):
    """get_history() returns list of decision entries."""
    result = mgr.get_history("sb1")

    assert stub.request.name == "sb1"
    assert len(result) == 1
    assert result[0]["event_type"] == "approved"
    assert result[0]["chunk_id"] == "chunk-1"


# ─── _chunk_to_dict conversion tests ────────────────────────────────────────


def test_chunk_to_dict_basic_fields():
    """Chunk without proposed_rule — basic fields only."""
    chunk = openshell_pb2.PolicyChunk(
        id="c1",
        status="pending",
        rule_name="pypi",
        rationale="Agent needs PyPI access",
        security_notes="Low risk",
        confidence=0.95,
        created_at_ms=1000,
        decided_at_ms=0,
        stage="initial",
        hit_count=3,
        first_seen_ms=500,
        last_seen_ms=900,
        binary="/usr/bin/pip",
        denial_summary_ids=["denial-a", "denial-b"],
    )
    result = _chunk_to_dict(chunk)

    assert result["id"] == "c1"
    assert result["status"] == "pending"
    assert result["rule_name"] == "pypi"
    assert result["rationale"] == "Agent needs PyPI access"
    assert result["security_notes"] == "Low risk"
    assert result["confidence"] == pytest.approx(0.95)
    assert result["hit_count"] == 3
    assert result["binary"] == "/usr/bin/pip"
    assert result["denial_summary_ids"] == ["denial-a", "denial-b"]
    assert "proposed_rule" not in result


def test_chunk_to_dict_denial_summary_ids_default_empty():
    """An absent/empty denial_summary_ids surfaces as an empty list, not None."""
    chunk = openshell_pb2.PolicyChunk(id="c1")
    result = _chunk_to_dict(chunk)
    assert result["denial_summary_ids"] == []


def test_chunk_to_dict_with_proposed_rule():
    """Chunk with proposed_rule including endpoints and binaries."""
    rule = sandbox_pb2.NetworkPolicyRule(
        name="pypi",
        endpoints=[
            sandbox_pb2.NetworkEndpoint(
                host="pypi.org",
                port=443,
                protocol="rest",
                tls="terminate",
                enforcement="enforce",
                access="full",
                allowed_ips=["1.2.3.4"],
                ports=[443],
                rules=[
                    sandbox_pb2.L7Rule(
                        allow=sandbox_pb2.L7Allow(method="GET", path="/**"),
                    ),
                ],
            ),
        ],
        binaries=[sandbox_pb2.NetworkBinary(path="/usr/bin/pip")],
    )
    chunk = openshell_pb2.PolicyChunk(
        id="c2",
        status="pending",
        rule_name="pypi",
        proposed_rule=rule,
    )
    result = _chunk_to_dict(chunk)

    assert "proposed_rule" in result
    pr = result["proposed_rule"]
    assert pr["name"] == "pypi"
    assert len(pr["endpoints"]) == 1
    ep = pr["endpoints"][0]
    assert ep["host"] == "pypi.org"
    assert ep["port"] == 443
    assert ep["protocol"] == "rest"
    assert ep["tls"] == "terminate"
    assert ep["enforcement"] == "enforce"
    assert ep["access"] == "full"
    assert ep["allowed_ips"] == ["1.2.3.4"]
    assert ep["ports"] == [443]
    assert len(ep["rules"]) == 1
    assert ep["rules"][0]["allow"]["method"] == "GET"
    assert len(pr["binaries"]) == 1
    assert pr["binaries"][0]["path"] == "/usr/bin/pip"


def test_get_pending_delegates(mgr):
    """get_pending() calls get_draft with status_filter='pending'."""
    result = mgr.get_pending("sb1")
    assert len(result) == 1
    assert result[0]["rule_name"] == "pypi"


# ─── Mutation-killing tests ──────────────────────────────────────────────────


def test_chunk_to_dict_timestamp_fields():
    """Assert all timestamp fields: created_at_ms, decided_at_ms, first_seen_ms, last_seen_ms."""
    chunk = openshell_pb2.PolicyChunk(
        id="c1",
        status="approved",
        rule_name="r1",
        created_at_ms=100,
        decided_at_ms=200,
        first_seen_ms=50,
        last_seen_ms=150,
        stage="review",
    )
    result = _chunk_to_dict(chunk)
    assert result["created_at_ms"] == 100
    assert result["decided_at_ms"] == 200
    assert result["first_seen_ms"] == 50
    assert result["last_seen_ms"] == 150
    assert result["stage"] == "review"


def test_chunk_to_dict_proposed_rule_minimal_endpoint():
    """Proposed rule with minimal endpoint (no optional fields)."""
    rule = sandbox_pb2.NetworkPolicyRule(
        name="minimal",
        endpoints=[sandbox_pb2.NetworkEndpoint(host="example.com", port=80)],
    )
    chunk = openshell_pb2.PolicyChunk(
        id="c3",
        status="pending",
        rule_name="minimal",
        proposed_rule=rule,
    )
    result = _chunk_to_dict(chunk)
    ep = result["proposed_rule"]["endpoints"][0]
    assert ep["host"] == "example.com"
    assert ep["port"] == 80
    for key in ("protocol", "tls", "enforcement", "access", "rules", "allowed_ips", "ports"):
        assert key not in ep


def test_get_draft_rolling_summary_and_last_analyzed(mgr):
    """get_draft() returns rolling_summary and last_analyzed_at_ms."""
    result = mgr.get_draft("sb1")
    assert result["rolling_summary"] == "summary"
    assert result["last_analyzed_at_ms"] == 1000


def test_approve_returns_policy_hash(mgr):
    """approve() returns policy_hash field."""
    result = mgr.approve("sb1", "chunk-1")
    assert result["policy_hash"] == "abc"


def test_approve_all_returns_policy_hash(mgr):
    """approve_all() returns policy_hash field."""
    result = mgr.approve_all("sb1")
    assert result["policy_hash"] == "abc"
    assert result["policy_version"] == 5


def test_undo_returns_policy_hash(mgr):
    """undo() returns policy_hash field."""
    result = mgr.undo("sb1", "chunk-1")
    assert result["policy_hash"] == "old"
    assert result["policy_version"] == 4


def test_get_history_timestamp_and_description(mgr):
    """get_history() returns timestamp_ms and description fields."""
    result = mgr.get_history("sb1")
    assert result[0]["timestamp_ms"] == 1000
    assert result[0]["description"] == "ok"


# ─── Additional mutation-killing tests ──────────────────────────────────────


class TestChunkToDictMutations:
    """Kill mutations in _chunk_to_dict field mappings."""

    def test_chunk_all_fields_exact(self):
        chunk = openshell_pb2.PolicyChunk(
            id="ID",
            status="STATUS",
            rule_name="RULE",
            rationale="RAT",
            security_notes="SEC",
            confidence=0.5,
            created_at_ms=1,
            decided_at_ms=2,
            stage="STAGE",
            hit_count=3,
            first_seen_ms=4,
            last_seen_ms=5,
            binary="BIN",
        )
        d = _chunk_to_dict(chunk)
        assert d["id"] == "ID"
        assert d["status"] == "STATUS"
        assert d["rule_name"] == "RULE"
        assert d["rationale"] == "RAT"
        assert d["security_notes"] == "SEC"
        assert d["confidence"] == pytest.approx(0.5)
        assert d["created_at_ms"] == 1
        assert d["decided_at_ms"] == 2
        assert d["stage"] == "STAGE"
        assert d["hit_count"] == 3
        assert d["first_seen_ms"] == 4
        assert d["last_seen_ms"] == 5
        assert d["binary"] == "BIN"

    def test_chunk_no_proposed_rule_key_absent(self):
        chunk = openshell_pb2.PolicyChunk(id="c")
        d = _chunk_to_dict(chunk)
        assert "proposed_rule" not in d

    def test_chunk_proposed_rule_name(self):
        rule = sandbox_pb2.NetworkPolicyRule(name="r1")
        chunk = openshell_pb2.PolicyChunk(id="c", proposed_rule=rule)
        d = _chunk_to_dict(chunk)
        assert d["proposed_rule"]["name"] == "r1"

    def test_chunk_proposed_rule_binaries(self):
        rule = sandbox_pb2.NetworkPolicyRule(
            name="r",
            binaries=[sandbox_pb2.NetworkBinary(path="/a"), sandbox_pb2.NetworkBinary(path="/b")],
        )
        chunk = openshell_pb2.PolicyChunk(id="c", proposed_rule=rule)
        d = _chunk_to_dict(chunk)
        assert d["proposed_rule"]["binaries"] == [{"path": "/a"}, {"path": "/b"}]

    def test_chunk_proposed_rule_empty_binaries(self):
        rule = sandbox_pb2.NetworkPolicyRule(name="r")
        chunk = openshell_pb2.PolicyChunk(id="c", proposed_rule=rule)
        d = _chunk_to_dict(chunk)
        assert d["proposed_rule"]["binaries"] == []

    def test_chunk_proposed_rule_endpoint_optional_fields_omitted(self):
        """Endpoint without protocol/tls/enforcement/access/rules/allowed_ips/ports omits them."""
        rule = sandbox_pb2.NetworkPolicyRule(
            name="r",
            endpoints=[sandbox_pb2.NetworkEndpoint(host="h", port=80)],
        )
        chunk = openshell_pb2.PolicyChunk(id="c", proposed_rule=rule)
        d = _chunk_to_dict(chunk)
        ep = d["proposed_rule"]["endpoints"][0]
        assert ep["host"] == "h"
        assert ep["port"] == 80
        for key in ("protocol", "tls", "enforcement", "access", "rules", "allowed_ips", "ports"):
            assert key not in ep

    def test_chunk_proposed_rule_endpoint_all_optional(self):
        """Endpoint with all optional fields present includes them."""
        rule = sandbox_pb2.NetworkPolicyRule(
            name="r",
            endpoints=[
                sandbox_pb2.NetworkEndpoint(
                    host="h",
                    port=443,
                    protocol="tcp",
                    tls="required",
                    enforcement="block",
                    access="allow",
                    allowed_ips=["1.1.1.1"],
                    ports=[443],
                    rules=[
                        sandbox_pb2.L7Rule(
                            allow=sandbox_pb2.L7Allow(method="GET", path="/x", command="curl")
                        )
                    ],
                )
            ],
        )
        chunk = openshell_pb2.PolicyChunk(id="c", proposed_rule=rule)
        d = _chunk_to_dict(chunk)
        ep = d["proposed_rule"]["endpoints"][0]
        assert ep["protocol"] == "tcp"
        assert ep["tls"] == "required"
        assert ep["enforcement"] == "block"
        assert ep["access"] == "allow"
        assert ep["allowed_ips"] == ["1.1.1.1"]
        assert ep["ports"] == [443]
        assert ep["rules"][0]["allow"]["method"] == "GET"
        assert ep["rules"][0]["allow"]["path"] == "/x"
        assert ep["rules"][0]["allow"]["command"] == "curl"


class TestApprovalManagerMutations:
    """Kill mutations in ApprovalManager method argument passing."""

    def test_get_draft_status_filter(self):
        class _Stub(_FakeStub):
            def GetDraftPolicy(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(
                    chunks=[], rolling_summary="", draft_version=0, last_analyzed_at_ms=0
                )

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.get_draft("sb", status_filter="approved")
        assert s.request.status_filter == "approved"

    def test_get_draft_default_status_filter_empty(self):
        class _Stub(_FakeStub):
            def GetDraftPolicy(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(
                    chunks=[], rolling_summary="", draft_version=0, last_analyzed_at_ms=0
                )

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.get_draft("sb")
        assert s.request.status_filter == ""

    def test_get_pending_uses_pending_filter(self):
        class _Stub(_FakeStub):
            def GetDraftPolicy(self, req, timeout=None):
                self.request = req
                return SimpleNamespace(
                    chunks=[openshell_pb2.PolicyChunk(id="c", status="pending")],
                    rolling_summary="",
                    draft_version=0,
                    last_analyzed_at_ms=0,
                )

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.get_pending("sb")
        assert s.request.status_filter == "pending"
        assert len(result) == 1

    def test_reject_default_reason_empty(self, mgr, stub):
        mgr.reject("sb1", "chunk-1")
        assert stub.request.reason == ""

    def test_approve_all_default_no_security_flagged(self, mgr, stub):
        mgr.approve_all("sb1")
        assert stub.request.include_security_flagged is False

    def test_approve_all_with_security_flagged(self, mgr, stub):
        mgr.approve_all("sb1", include_security_flagged=True)
        assert stub.request.include_security_flagged is True

    def test_edit_passes_rule_with_endpoints(self):
        class _Stub(_FakeStub):
            def EditDraftChunk(self, req, timeout=None):
                self.request = req
                return SimpleNamespace()

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        m.edit(
            "sb",
            "c",
            {
                "name": "rule",
                "endpoints": [{"host": "h", "port": 80}],
                "binaries": [{"path": "/bin/x"}],
            },
        )
        assert s.request.proposed_rule.name == "rule"
        assert s.request.proposed_rule.endpoints[0].host == "h"
        assert s.request.proposed_rule.endpoints[0].port == 80
        assert s.request.proposed_rule.binaries[0].path == "/bin/x"

    def test_undo_sends_sandbox_name(self, mgr, stub):
        mgr.undo("sb1", "chunk-1")
        assert stub.request.name == "sb1"

    def test_clear_sends_sandbox_name(self, mgr, stub):
        mgr.clear("sb1")
        assert stub.request.name == "sb1"

    def test_get_history_entries_all_fields(self):
        class _Stub(_FakeStub):
            def GetDraftHistory(self, req, timeout=None):
                return SimpleNamespace(
                    entries=[
                        SimpleNamespace(
                            timestamp_ms=100,
                            event_type="rejected",
                            description="bad",
                            chunk_id="c2",
                        ),
                        SimpleNamespace(
                            timestamp_ms=200, event_type="approved", description="ok", chunk_id="c3"
                        ),
                    ]
                )

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.get_history("sb")
        assert len(result) == 2
        assert result[0] == {
            "timestamp_ms": 100,
            "event_type": "rejected",
            "description": "bad",
            "chunk_id": "c2",
        }
        assert result[1] == {
            "timestamp_ms": 200,
            "event_type": "approved",
            "description": "ok",
            "chunk_id": "c3",
        }

    def test_get_draft_all_return_fields(self):
        class _Stub(_FakeStub):
            def GetDraftPolicy(self, req, timeout=None):
                return SimpleNamespace(
                    chunks=[], rolling_summary="RS", draft_version=9, last_analyzed_at_ms=555
                )

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.get_draft("sb")
        assert result == {
            "chunks": [],
            "rolling_summary": "RS",
            "draft_version": 9,
            "last_analyzed_at_ms": 555,
        }

    def test_approve_returns_exact_dict(self):
        class _Stub(_FakeStub):
            def ApproveDraftChunk(self, req, timeout=None):
                return SimpleNamespace(policy_version=10, policy_hash="HASH")

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.approve("sb", "c")
        assert result == {"policy_version": 10, "policy_hash": "HASH"}

    def test_clear_returns_exact_dict(self):
        class _Stub(_FakeStub):
            def ClearDraftChunks(self, req, timeout=None):
                return SimpleNamespace(chunks_cleared=0)

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.clear("sb")
        assert result == {"chunks_cleared": 0}

    def test_approve_all_returns_exact_dict(self):
        class _Stub(_FakeStub):
            def ApproveAllDraftChunks(self, req, timeout=None):
                return SimpleNamespace(
                    policy_version=7, policy_hash="H", chunks_approved=0, chunks_skipped=0
                )

        s = _Stub()
        m = object.__new__(ApprovalManager)
        m._stub = s  # type: ignore[assignment]
        m._timeout = 30.0
        result = m.approve_all("sb")
        assert result == {
            "policy_version": 7,
            "policy_hash": "H",
            "chunks_approved": 0,
            "chunks_skipped": 0,
        }
