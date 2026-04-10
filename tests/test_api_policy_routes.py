"""Integration tests for policy API routes."""

from __future__ import annotations

from shoreguard.exceptions import NotFoundError

GW = "test"
SB = "sb1"
BASE = f"/api/gateways/{GW}/sandboxes/{SB}/policy"


async def test_get_policy(api_client, mock_client):
    """GET /policy returns current sandbox policy."""
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}

    resp = await api_client.get(BASE)

    assert resp.status_code == 200
    assert "policy" in resp.json()


async def test_get_effective_policy(api_client, mock_client):
    """GET /policy/effective returns the enforced policy with source marker."""
    mock_client.policies.get.return_value = {
        "active_version": 3,
        "revision": {"version": 3, "status": "loaded"},
        "policy": {"network_policies": {"pypi": {}}},
    }

    resp = await api_client.get(f"{BASE}/effective")

    assert resp.status_code == 200
    data = resp.json()
    assert data["active_version"] == 3
    assert data["policy"]["network_policies"] == {"pypi": {}}
    assert data["source"] == "gateway_runtime"


async def test_get_effective_policy_not_found(api_client, mock_client):
    """GET /policy/effective returns 404 when the sandbox is missing."""
    mock_client.policies.get.side_effect = NotFoundError("Sandbox not found")

    resp = await api_client.get(f"{BASE}/effective")

    assert resp.status_code == 404


async def test_submit_policy_analysis(api_client, mock_client):
    """POST /policy/analysis forwards the request body to the client."""
    mock_client.policies.submit_analysis.return_value = {
        "accepted_chunks": 2,
        "rejected_chunks": 1,
        "rejection_reasons": ["conflicts with rule foo"],
    }

    body = {
        "summaries": [
            {
                "sandbox_id": "sb1",
                "host": "api.example.com",
                "port": 443,
                "binary": "/usr/bin/curl",
                "count": 3,
            }
        ],
        "proposed_chunks": [
            {
                "id": "chunk-1",
                "rule_name": "allow_example_api",
                "rationale": "3 denials on same host",
            }
        ],
        "analysis_mode": "auto",
    }
    resp = await api_client.post(f"{BASE}/analysis", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted_chunks"] == 2
    assert data["rejected_chunks"] == 1
    assert data["rejection_reasons"] == ["conflicts with rule foo"]

    # Client was called with the unpacked kwargs.
    call = mock_client.policies.submit_analysis.call_args
    assert call.args == ("sb1",)
    assert call.kwargs["analysis_mode"] == "auto"
    assert len(call.kwargs["summaries"]) == 1
    assert call.kwargs["summaries"][0]["host"] == "api.example.com"
    assert len(call.kwargs["proposed_chunks"]) == 1


async def test_submit_policy_analysis_empty_body_defaults(api_client, mock_client):
    """Empty request body succeeds with the Pydantic defaults."""
    mock_client.policies.submit_analysis.return_value = {
        "accepted_chunks": 0,
        "rejected_chunks": 0,
        "rejection_reasons": [],
    }

    resp = await api_client.post(f"{BASE}/analysis", json={})

    assert resp.status_code == 200
    call = mock_client.policies.submit_analysis.call_args
    assert call.kwargs["summaries"] == []
    assert call.kwargs["proposed_chunks"] == []
    assert call.kwargs["analysis_mode"] == ""


async def test_submit_policy_analysis_unknown_field_rejected(api_client, mock_client):
    """Unknown top-level fields in the request body are rejected (extra='forbid')."""
    resp = await api_client.post(
        f"{BASE}/analysis",
        json={"summaries": [], "proposed_chunks": [], "bogus": 42},
    )
    assert resp.status_code == 422


async def test_submit_policy_analysis_writes_audit_log(api_client, mock_client):
    """An audit entry is written with accepted/rejected counters in the detail."""
    from unittest.mock import patch

    mock_client.policies.submit_analysis.return_value = {
        "accepted_chunks": 5,
        "rejected_chunks": 0,
        "rejection_reasons": [],
    }

    with patch("shoreguard.api.routes.policies.audit_log") as mock_audit:
        resp = await api_client.post(
            f"{BASE}/analysis",
            json={"summaries": [], "proposed_chunks": [], "analysis_mode": "auto"},
        )

    assert resp.status_code == 200
    mock_audit.assert_called_once()
    call = mock_audit.call_args
    # Positional: (request, action, resource_type, resource_id)
    assert call.args[1] == "sandbox.policy.analyze"
    assert call.args[2] == "sandbox"
    assert call.args[3] == "sb1"
    detail = call.kwargs["detail"]
    assert detail["analysis_mode"] == "auto"
    assert detail["accepted"] == 5
    assert detail["rejected"] == 0


async def test_update_policy(api_client, mock_client):
    """PUT /policy returns full PolicyResponse after update."""
    mock_client.policies.update.return_value = {"version": 3, "policy_hash": "abc"}
    mock_client.policies.get.return_value = {
        "active_version": 3,
        "revision": {"version": 3, "status": "loaded", "policy_hash": "abc"},
        "policy": {"network_policies": {}},
    }

    resp = await api_client.put(BASE, json={"network_policies": {}})

    assert resp.status_code == 200
    data = resp.json()
    assert data["active_version"] == 3
    assert "policy" in data


async def test_list_revisions(api_client, mock_client):
    """GET /policy/revisions returns revision list."""
    mock_client.policies.list_revisions.return_value = [{"revision": 1}, {"revision": 2}]

    resp = await api_client.get(f"{BASE}/revisions")

    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_add_network_rule(api_client, mock_client):
    """POST /policy/network-rules adds a rule."""
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}
    mock_client.policies.update.return_value = {"revision": 2}

    resp = await api_client.post(
        f"{BASE}/network-rules",
        json={"key": "pypi", "rule": {"endpoints": []}},
    )

    assert resp.status_code == 200


async def test_delete_network_rule(api_client, mock_client):
    """DELETE /policy/network-rules/{key} removes a rule."""
    mock_client.policies.get.return_value = {
        "policy": {"status": "loaded", "network_policies": {"my_rule": {}}}
    }
    mock_client.policies.update.return_value = {"revision": 3}

    resp = await api_client.delete(f"{BASE}/network-rules/my_rule")

    assert resp.status_code == 200


async def test_add_filesystem_path(api_client, mock_client):
    """POST /policy/filesystem adds a path."""
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}
    mock_client.policies.update.return_value = {"revision": 2}

    resp = await api_client.post(
        f"{BASE}/filesystem",
        json={"path": "/tmp", "access": "ro"},
    )

    assert resp.status_code == 200


async def test_delete_filesystem_path(api_client, mock_client):
    """DELETE /policy/filesystem?path=... removes a path."""
    mock_client.policies.get.return_value = {
        "policy": {
            "status": "loaded",
            "filesystem": {"read_only": ["/tmp"], "read_write": [], "include_workdir": False},
        }
    }
    mock_client.policies.update.return_value = {"revision": 3}

    resp = await api_client.delete(f"{BASE}/filesystem", params={"path": "/tmp"})

    assert resp.status_code == 200


async def test_update_process_policy(api_client, mock_client):
    """PUT /policy/process sets process/landlock settings."""
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}
    mock_client.policies.update.return_value = {"revision": 2}

    resp = await api_client.put(
        f"{BASE}/process",
        json={"run_as_user": "nobody", "run_as_group": "nogroup"},
    )

    assert resp.status_code == 200


async def test_apply_preset(api_client, mock_client):
    """POST /policy/presets/pypi applies the pypi preset."""
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}
    mock_client.policies.update.return_value = {"revision": 2}

    resp = await api_client.post(f"{BASE}/presets/pypi")

    assert resp.status_code == 200


async def test_get_valid_preset(api_client):
    """GET /api/policies/presets/pypi returns PresetDetail format."""
    resp = await api_client.get("/api/policies/presets/pypi")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "pypi"
    assert "description" in data
    assert "network_policies" in data["policy"]


async def test_update_policy_nonexistent_sandbox(api_client, mock_client):
    """PUT /policy returns 404 when sandbox doesn't exist."""
    mock_client.policies.update.side_effect = NotFoundError("Sandbox not found")

    resp = await api_client.put(BASE, json={"network_policies": {}})

    assert resp.status_code == 404


async def test_add_network_rule_nonexistent_sandbox(api_client, mock_client):
    """POST /policy/network-rules returns 404 when sandbox doesn't exist."""
    mock_client.policies.get.side_effect = NotFoundError("Sandbox not found")

    resp = await api_client.post(
        f"{BASE}/network-rules",
        json={"key": "pypi", "rule": {"endpoints": []}},
    )

    assert resp.status_code == 404


async def test_apply_nonexistent_preset(api_client, mock_client):
    """POST /policy/presets/{name} returns 404 for unknown preset."""
    resp = await api_client.post(f"{BASE}/presets/nonexistent-preset-xyz")

    assert resp.status_code == 404
