"""Integration tests for approval API routes."""

from __future__ import annotations

from shoreguard.exceptions import NotFoundError

GW = "test"
SB = "sb1"
BASE = f"/api/gateways/{GW}/sandboxes/{SB}/approvals"
CHUNK = "chunk-abc"


async def test_get_approvals(api_client, mock_client):
    """GET /approvals returns draft policy data."""
    mock_client.approvals.get_draft.return_value = {"chunks": [], "status": "empty"}

    resp = await api_client.get(BASE)

    assert resp.status_code == 200
    assert "chunks" in resp.json()


async def test_get_pending_approvals(api_client, mock_client):
    """GET /approvals/pending returns pending chunks."""
    mock_client.approvals.get_pending.return_value = [{"id": CHUNK}]

    resp = await api_client.get(f"{BASE}/pending")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_approve_nonexistent_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/approve returns 404 for unknown chunk."""
    mock_client.approvals.approve.side_effect = NotFoundError("Chunk not found")

    resp = await api_client.post(f"{BASE}/nonexistent-chunk/approve")

    assert resp.status_code == 404


async def test_reject_nonexistent_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/reject returns 404 for unknown chunk."""
    mock_client.approvals.reject.side_effect = NotFoundError("Chunk not found")

    resp = await api_client.post(
        f"{BASE}/nonexistent-chunk/reject",
        json={"reason": "test"},
    )

    assert resp.status_code == 404


async def test_edit_nonexistent_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/edit returns 404 for unknown chunk."""
    mock_client.approvals.edit.side_effect = NotFoundError("Chunk not found")

    resp = await api_client.post(
        f"{BASE}/nonexistent-chunk/edit",
        json={"proposed_rule": {"key": "r1", "rule": {}}},
    )

    assert resp.status_code == 404


async def test_undo_nonexistent_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/undo returns 404 for unknown chunk."""
    mock_client.approvals.undo.side_effect = NotFoundError("Chunk not found")

    resp = await api_client.post(f"{BASE}/nonexistent-chunk/undo")

    assert resp.status_code == 404


async def test_approve_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/approve approves a chunk."""
    mock_client.approvals.approve.return_value = {"id": CHUNK, "status": "approved"}

    resp = await api_client.post(f"{BASE}/{CHUNK}/approve")

    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


async def test_reject_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/reject rejects with a reason."""
    mock_client.approvals.reject.return_value = None

    resp = await api_client.post(
        f"{BASE}/{CHUNK}/reject",
        json={"reason": "too permissive"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


async def test_approve_all(api_client, mock_client):
    """POST /approvals/approve-all bulk-approves all pending chunks."""
    mock_client.approvals.approve_all.return_value = {"approved": 3, "skipped": 0}

    resp = await api_client.post(f"{BASE}/approve-all")

    assert resp.status_code == 200
    assert resp.json()["approved"] == 3


async def test_edit_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/edit edits a chunk's proposed rule."""
    mock_client.approvals.edit.return_value = None

    resp = await api_client.post(
        f"{BASE}/{CHUNK}/edit",
        json={"proposed_rule": {"key": "updated_rule", "rule": {}}},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "edited"


async def test_undo_chunk(api_client, mock_client):
    """POST /approvals/{chunk_id}/undo reverses an approval decision."""
    mock_client.approvals.undo.return_value = {"id": CHUNK, "status": "pending"}

    resp = await api_client.post(f"{BASE}/{CHUNK}/undo")

    assert resp.status_code == 200


async def test_clear_approvals(api_client, mock_client):
    """POST /approvals/clear removes all pending chunks."""
    mock_client.approvals.clear.return_value = {"cleared": 5}

    resp = await api_client.post(f"{BASE}/clear")

    assert resp.status_code == 200


async def test_get_approval_history(api_client, mock_client):
    """GET /approvals/history returns decision history."""
    mock_client.approvals.get_history.return_value = [{"id": CHUNK, "decision": "approved"}]

    resp = await api_client.get(f"{BASE}/history")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ─── wait_loaded ─────────────────────────────────────────────────────────────


async def test_approve_chunk_wait_loaded(api_client, mock_client, monkeypatch):
    """Approve with ?wait_loaded=true awaits the policy-status broker."""
    from shoreguard.services import policy_status as ps_mod

    calls: list[tuple] = []

    async def fake_wait(client, sandbox_name, target_version, *, timeout=30.0, slow_poll=2.0):
        calls.append((sandbox_name, target_version))

    monkeypatch.setattr(ps_mod.policy_status_broker, "wait_for_loaded", fake_wait)

    mock_client.approvals.approve.return_value = {
        "id": CHUNK,
        "status": "approved",
        "policy_version": 5,
    }

    resp = await api_client.post(f"{BASE}/{CHUNK}/approve?wait_loaded=true")

    assert resp.status_code == 200
    assert calls == [(SB, 5)]


async def test_approve_chunk_wait_loaded_timeout(api_client, mock_client, monkeypatch):
    """Approve with ?wait_loaded=true returns 504 when broker times out."""
    from fastapi import HTTPException

    from shoreguard.services import policy_status as ps_mod

    async def fake_wait(client, sandbox_name, target_version, *, timeout=30.0, slow_poll=2.0):
        raise HTTPException(status_code=504, detail="timeout")

    monkeypatch.setattr(ps_mod.policy_status_broker, "wait_for_loaded", fake_wait)

    mock_client.approvals.approve.return_value = {
        "id": CHUNK,
        "status": "approved",
        "policy_version": 5,
    }

    resp = await api_client.post(f"{BASE}/{CHUNK}/approve?wait_loaded=true")

    assert resp.status_code == 504


async def test_approve_chunk_without_wait_loaded_skips_broker(api_client, mock_client, monkeypatch):
    """Approve without wait_loaded does not invoke the policy-status broker."""
    from shoreguard.services import policy_status as ps_mod

    called = False

    async def fake_wait(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(ps_mod.policy_status_broker, "wait_for_loaded", fake_wait)

    mock_client.approvals.approve.return_value = {
        "id": CHUNK,
        "status": "approved",
        "policy_version": 5,
    }

    resp = await api_client.post(f"{BASE}/{CHUNK}/approve")

    assert resp.status_code == 200
    assert called is False


async def test_approve_all_wait_loaded(api_client, mock_client, monkeypatch):
    """Approve-all with ?wait_loaded=true awaits the policy-status broker."""
    from shoreguard.services import policy_status as ps_mod

    calls: list[tuple] = []

    async def fake_wait(client, sandbox_name, target_version, *, timeout=30.0, slow_poll=2.0):
        calls.append((sandbox_name, target_version))

    monkeypatch.setattr(ps_mod.policy_status_broker, "wait_for_loaded", fake_wait)

    mock_client.approvals.approve_all.return_value = {
        "approved": 3,
        "skipped": 0,
        "policy_version": 7,
    }

    resp = await api_client.post(f"{BASE}/approve-all?wait_loaded=true")

    assert resp.status_code == 200
    assert calls == [(SB, 7)]
