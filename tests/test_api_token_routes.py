"""Integration tests for gateway sandbox-token routes."""

from __future__ import annotations

GW = "test"
BASE = f"/api/gateways/{GW}/tokens"


async def test_issue_token(api_client, mock_client):
    """POST /tokens/issue returns the minted token and expiry."""
    mock_client.sandboxes.issue_token.return_value = {"token": "jwt-x", "expires_at_ms": 1000}

    resp = await api_client.post(f"{BASE}/issue")

    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "jwt-x"
    assert body["expires_at_ms"] == 1000


async def test_refresh_token(api_client, mock_client):
    """POST /tokens/refresh returns a fresh token."""
    mock_client.sandboxes.refresh_token.return_value = {"token": "jwt-y", "expires_at_ms": 0}

    resp = await api_client.post(f"{BASE}/refresh")

    assert resp.status_code == 200
    assert resp.json()["token"] == "jwt-y"
