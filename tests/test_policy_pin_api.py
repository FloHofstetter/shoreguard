"""Integration tests for policy pin API routes."""

from __future__ import annotations

import pytest

GW = "test"
SB = "sb1"
PIN_URL = f"/api/gateways/{GW}/sandboxes/{SB}/policy/pin"
POLICY_URL = f"/api/gateways/{GW}/sandboxes/{SB}/policy"


def _mock_policy(mock_client, version=5):
    """Configure mock to return a policy with an active version."""
    mock_client.policies.get.return_value = {
        "active_version": version,
        "revision": {"version": version, "status": "loaded"},
        "policy": {"network_policies": {"rule1": {}}},
    }


class TestGetPin:
    async def test_get_pin_404_when_not_pinned(self, api_client):
        resp = await api_client.get(PIN_URL)
        assert resp.status_code == 404

    async def test_get_pin_after_pinning(self, api_client, mock_client):
        _mock_policy(mock_client)
        await api_client.post(PIN_URL, json={"reason": "freeze"})

        resp = await api_client.get(PIN_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["pinned_version"] == 5
        assert data["reason"] == "freeze"
        assert data["sandbox_name"] == SB


class TestPinPolicy:
    async def test_pin_creates_pin(self, api_client, mock_client):
        _mock_policy(mock_client)
        resp = await api_client.post(PIN_URL, json={"reason": "deploy freeze"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["pinned_version"] == 5
        assert data["pinned_by"] == "no-auth"  # _disable_auth fixture sets this
        assert data["reason"] == "deploy freeze"

    async def test_pin_without_body(self, api_client, mock_client):
        _mock_policy(mock_client)
        resp = await api_client.post(PIN_URL)

        assert resp.status_code == 200
        data = resp.json()
        assert data["reason"] is None

    async def test_pin_with_expiry(self, api_client, mock_client):
        _mock_policy(mock_client)
        resp = await api_client.post(
            PIN_URL,
            json={"reason": "temp", "expires_at": "2099-01-01T00:00:00+00:00"},
        )

        assert resp.status_code == 200
        assert resp.json()["expires_at"] is not None

    async def test_pin_upserts(self, api_client, mock_client):
        _mock_policy(mock_client, version=3)
        await api_client.post(PIN_URL, json={"reason": "v1"})

        _mock_policy(mock_client, version=7)
        resp = await api_client.post(PIN_URL, json={"reason": "v2"})

        assert resp.status_code == 200
        assert resp.json()["pinned_version"] == 7
        assert resp.json()["reason"] == "v2"


class TestUnpinPolicy:
    async def test_unpin_existing(self, api_client, mock_client):
        _mock_policy(mock_client)
        await api_client.post(PIN_URL)

        resp = await api_client.delete(PIN_URL)
        assert resp.status_code == 204

        # Should be gone now
        resp = await api_client.get(PIN_URL)
        assert resp.status_code == 404

    async def test_unpin_nonexistent(self, api_client):
        resp = await api_client.delete(PIN_URL)
        assert resp.status_code == 404


class TestPinGuardOnPolicyRoutes:
    """Verify that policy write endpoints return 423 when pinned."""

    @pytest.fixture(autouse=True)
    async def _pin_policy(self, api_client, mock_client):
        _mock_policy(mock_client)
        await api_client.post(PIN_URL, json={"reason": "locked"})

    async def test_update_policy_blocked(self, api_client, mock_client):
        resp = await api_client.put(
            POLICY_URL,
            json={"network_policies": {"rule1": {}}},
        )
        assert resp.status_code == 423
        assert "pinned" in resp.json()["detail"].lower()

    async def test_add_network_rule_blocked(self, api_client):
        resp = await api_client.post(
            f"{POLICY_URL}/network-rules",
            json={"key": "newrule", "rule": {"endpoints": []}},
        )
        assert resp.status_code == 423

    async def test_delete_network_rule_blocked(self, api_client):
        resp = await api_client.delete(f"{POLICY_URL}/network-rules/rule1")
        assert resp.status_code == 423

    async def test_add_filesystem_path_blocked(self, api_client):
        resp = await api_client.post(
            f"{POLICY_URL}/filesystem",
            json={"path": "/tmp", "access": "ro"},
        )
        assert resp.status_code == 423

    async def test_delete_filesystem_path_blocked(self, api_client):
        resp = await api_client.delete(f"{POLICY_URL}/filesystem", params={"path": "/tmp"})
        assert resp.status_code == 423

    async def test_update_process_policy_blocked(self, api_client):
        resp = await api_client.put(
            f"{POLICY_URL}/process",
            json={"run_as_user": "nobody"},
        )
        assert resp.status_code == 423

    async def test_apply_preset_blocked(self, api_client):
        resp = await api_client.post(f"{POLICY_URL}/presets/default")
        assert resp.status_code == 423

    async def test_get_policy_still_works(self, api_client, mock_client):
        """Read endpoints should NOT be blocked by a pin."""
        resp = await api_client.get(POLICY_URL)
        assert resp.status_code == 200


class TestPinGuardOnApprovalRoutes:
    """Verify that approval endpoints return 423 when pinned."""

    @pytest.fixture(autouse=True)
    async def _pin_policy(self, api_client, mock_client):
        _mock_policy(mock_client)
        await api_client.post(PIN_URL, json={"reason": "locked"})

    async def test_approve_chunk_blocked(self, api_client):
        resp = await api_client.post(f"/api/gateways/{GW}/sandboxes/{SB}/approvals/chunk-1/approve")
        assert resp.status_code == 423

    async def test_approve_all_blocked(self, api_client):
        resp = await api_client.post(
            f"/api/gateways/{GW}/sandboxes/{SB}/approvals/approve-all",
            json={"include_security_flagged": False},
        )
        assert resp.status_code == 423

    async def test_get_approvals_still_works(self, api_client, mock_client):
        """Read endpoints should NOT be blocked by a pin."""
        mock_client.approvals.get_draft.return_value = {
            "chunks": [],
            "rolling_summary": "",
            "draft_version": 0,
            "last_analyzed_at_ms": 0,
        }
        resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/{SB}/approvals")
        assert resp.status_code == 200
