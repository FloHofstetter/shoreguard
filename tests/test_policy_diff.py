"""Tests for policy diff / revision history feature."""

from __future__ import annotations

import pytest

from shoreguard.services.policy import PolicyService

# ── Fixtures ─────────────────────────────────────────────────────────────────

VERSION_1_RESPONSE = {
    "policy": {"network_policies": {}},
    "revision": {"version": 1, "created_at": "2026-01-01T00:00:00"},
}

VERSION_2_RESPONSE = {
    "policy": {"network_policies": {"allow_dns": {"name": "dns"}}},
    "revision": {"version": 2, "created_at": "2026-01-02T00:00:00"},
}

REVISIONS_LIST = [
    {"version": 1, "created_at": "2026-01-01T00:00:00"},
    {"version": 2, "created_at": "2026-01-02T00:00:00"},
]


@pytest.fixture
def policy_svc(mock_client):
    """PolicyService backed by a mocked client."""
    return PolicyService(mock_client)


# ── Unit tests: PolicyService ────────────────────────────────────────────────


def test_get_version_delegates_to_client(policy_svc, mock_client):
    """get_version passes sandbox name and version to the client."""
    mock_client.policies.get_version.return_value = VERSION_1_RESPONSE

    result = policy_svc.get_version("sb1", 1)

    mock_client.policies.get_version.assert_called_once_with("sb1", 1)
    assert result == VERSION_1_RESPONSE


def test_diff_revisions_returns_structured_diff(policy_svc, mock_client):
    """diff_revisions fetches two versions and returns a structured dict."""
    mock_client.policies.get_version.side_effect = [
        VERSION_1_RESPONSE,
        VERSION_2_RESPONSE,
    ]

    result = policy_svc.diff_revisions("sb1", 1, 2)

    assert result["version_a"] == 1
    assert result["version_b"] == 2
    assert result["policy_a"] == VERSION_1_RESPONSE["policy"]
    assert result["policy_b"] == VERSION_2_RESPONSE["policy"]
    assert result["revision_a"] == VERSION_1_RESPONSE["revision"]
    assert result["revision_b"] == VERSION_2_RESPONSE["revision"]
    assert mock_client.policies.get_version.call_count == 2


def test_list_revisions_delegates_with_limit_offset(policy_svc, mock_client):
    """list_revisions passes limit and offset to the client."""
    mock_client.policies.list_revisions.return_value = REVISIONS_LIST

    result = policy_svc.list_revisions("sb1", limit=10, offset=5)

    mock_client.policies.list_revisions.assert_called_once_with("sb1", limit=10, offset=5)
    assert result == REVISIONS_LIST


# ── API endpoint tests ───────────────────────────────────────────────────────

GW_PREFIX = "/api/gateways/test"


@pytest.mark.anyio
async def test_api_list_revisions(api_client, mock_client):
    """GET .../policy/revisions returns the revision list."""
    mock_client.policies.list_revisions.return_value = REVISIONS_LIST

    resp = await api_client.get(f"{GW_PREFIX}/sandboxes/sb1/policy/revisions")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["version"] == 1
    assert data[1]["version"] == 2


@pytest.mark.anyio
async def test_api_diff_revisions(api_client, mock_client):
    """GET .../policy/diff?version_a=1&version_b=2 returns the diff."""
    mock_client.policies.get_version.side_effect = [
        VERSION_1_RESPONSE,
        VERSION_2_RESPONSE,
    ]

    resp = await api_client.get(
        f"{GW_PREFIX}/sandboxes/sb1/policy/diff",
        params={"version_a": 1, "version_b": 2},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["version_a"] == 1
    assert data["version_b"] == 2
    assert data["policy_a"] == VERSION_1_RESPONSE["policy"]
    assert data["policy_b"] == VERSION_2_RESPONSE["policy"]


@pytest.mark.anyio
async def test_api_diff_requires_both_params(api_client, mock_client):
    """diff endpoint returns 422 when version_a or version_b is missing."""
    resp_no_b = await api_client.get(
        f"{GW_PREFIX}/sandboxes/sb1/policy/diff",
        params={"version_a": 1},
    )
    assert resp_no_b.status_code == 422

    resp_no_a = await api_client.get(
        f"{GW_PREFIX}/sandboxes/sb1/policy/diff",
        params={"version_b": 2},
    )
    assert resp_no_a.status_code == 422

    resp_none = await api_client.get(
        f"{GW_PREFIX}/sandboxes/sb1/policy/diff",
    )
    assert resp_none.status_code == 422


@pytest.mark.anyio
async def test_api_diff_rejects_version_below_one(api_client, mock_client):
    """diff endpoint returns 422 when version < 1."""
    resp = await api_client.get(
        f"{GW_PREFIX}/sandboxes/sb1/policy/diff",
        params={"version_a": 0, "version_b": 2},
    )
    assert resp.status_code == 422

    resp2 = await api_client.get(
        f"{GW_PREFIX}/sandboxes/sb1/policy/diff",
        params={"version_a": 1, "version_b": -1},
    )
    assert resp2.status_code == 422
