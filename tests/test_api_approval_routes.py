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


async def test_approve_chunk_wait_loaded(api_client, mock_client):
    """Approve with ?wait_loaded=true polls until policy is loaded."""
    mock_client.approvals.approve.return_value = {
        "id": CHUNK,
        "status": "approved",
        "policy_version": 5,
    }
    mock_client.policies.get.return_value = {
        "active_version": 5,
        "revision": {"status": "loaded"},
    }

    resp = await api_client.post(f"{BASE}/{CHUNK}/approve?wait_loaded=true")

    assert resp.status_code == 200
    mock_client.policies.get.assert_called_with(SB)


async def test_approve_chunk_wait_loaded_timeout(api_client, mock_client, monkeypatch):
    """Approve with ?wait_loaded=true returns 504 on timeout."""
    from shoreguard.api.routes import approvals as approvals_mod

    monkeypatch.setattr(approvals_mod, "_POLICY_POLL_TIMEOUT", 2)
    monkeypatch.setattr(approvals_mod, "_POLICY_POLL_INTERVAL", 0.1)

    mock_client.approvals.approve.return_value = {
        "id": CHUNK,
        "status": "approved",
        "policy_version": 5,
    }
    mock_client.policies.get.return_value = {
        "active_version": 4,
        "revision": {"status": "pending"},
    }

    resp = await api_client.post(f"{BASE}/{CHUNK}/approve?wait_loaded=true")

    assert resp.status_code == 504


async def test_approve_chunk_without_wait_loaded_skips_poll(api_client, mock_client):
    """Approve without wait_loaded does not poll policy status."""
    mock_client.approvals.approve.return_value = {
        "id": CHUNK,
        "status": "approved",
        "policy_version": 5,
    }

    resp = await api_client.post(f"{BASE}/{CHUNK}/approve")

    assert resp.status_code == 200
    mock_client.policies.get.assert_not_called()


async def test_approve_all_wait_loaded(api_client, mock_client):
    """Approve-all with ?wait_loaded=true polls until policy is loaded."""
    mock_client.approvals.approve_all.return_value = {
        "approved": 3,
        "skipped": 0,
        "policy_version": 7,
    }
    mock_client.policies.get.return_value = {
        "active_version": 7,
        "revision": {"status": "loaded"},
    }

    resp = await api_client.post(f"{BASE}/approve-all?wait_loaded=true")

    assert resp.status_code == 200
    mock_client.policies.get.assert_called_with(SB)
