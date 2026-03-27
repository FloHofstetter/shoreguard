"""Integration tests for policy API routes."""

from __future__ import annotations

GW = "test"
SB = "sb1"
BASE = f"/api/gateways/{GW}/sandboxes/{SB}/policy"


async def test_get_policy(api_client, mock_client):
    """GET /policy returns current sandbox policy."""
    mock_client.policies.get.return_value = {"policy": {"status": "loaded"}}

    resp = await api_client.get(BASE)

    assert resp.status_code == 200
    assert "policy" in resp.json()


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
