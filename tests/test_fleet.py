"""Tests for the cross-gateway fleet service and routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shoreguard.services.fleet import FleetService


def _make_client(
    sandboxes: list[str],
    hashes: dict[str, str],
    policy: dict | None = None,
) -> MagicMock:
    client = MagicMock()
    client.sandboxes.list = AsyncMock(return_value=[{"name": n} for n in sandboxes])

    async def _get(sb_name: str):
        if policy is not None:
            return {"policy": policy, "revision": {"policy_hash": hashes.get(sb_name, "")}}
        return {"revision": {"policy_hash": hashes.get(sb_name, "")}}

    client.policies.get = AsyncMock(side_effect=_get)
    client.policies.update = AsyncMock(return_value={"version": 2})
    return client


class _FakeGateways:
    def __init__(self, clients: dict[str, MagicMock], versions: dict[str, str]) -> None:
        self._clients = clients
        self._versions = versions

    async def get_client(self, name: str):
        client = self._clients.get(name)
        if client is None:
            raise RuntimeError(f"gateway '{name}' unreachable")
        return client

    def known_versions(self) -> dict[str, str]:
        return dict(self._versions)


class _FakeRegistry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def list_all(self, **kwargs):
        return [{"name": n, "last_status": "connected"} for n in self._names]


def _fleet(clients: dict[str, MagicMock], versions=None, names=None) -> FleetService:
    return FleetService(
        _FakeRegistry(names or list(clients.keys())),  # type: ignore[arg-type]
        _FakeGateways(clients, versions or {}),  # type: ignore[arg-type]
    )


async def test_overview_collects_versions_and_hashes() -> None:
    svc = _fleet(
        {
            "gw1": _make_client(["agent-a", "agent-b"], {"agent-a": "h1", "agent-b": "h2"}),
            "gw2": _make_client(["agent-a"], {"agent-a": "h1"}),
        },
        versions={"gw1": "0.6.0", "gw2": "0.5.0"},
    )

    overview = await svc.overview()

    by_name = {g["name"]: g for g in overview}
    assert by_name["gw1"]["version"] == "0.6.0"
    assert by_name["gw1"]["sandbox_count"] == 2
    assert by_name["gw1"]["sandboxes"]["agent-a"] == "h1"
    assert by_name["gw2"]["reachable"] is True


async def test_overview_tolerates_unreachable_gateway() -> None:
    svc = _fleet(
        {"gw1": _make_client(["agent-a"], {"agent-a": "h1"})},
        names=["gw1", "gw-dead"],
    )

    overview = await svc.overview()

    by_name = {g["name"]: g for g in overview}
    assert by_name["gw-dead"]["reachable"] is False
    assert by_name["gw-dead"]["sandboxes"] == {}
    assert by_name["gw1"]["reachable"] is True


async def test_policy_drift_flags_differing_hashes() -> None:
    svc = _fleet(
        {
            "gw1": _make_client(["agent-a", "solo"], {"agent-a": "h1", "solo": "x"}),
            "gw2": _make_client(["agent-a", "agent-b"], {"agent-a": "h2", "agent-b": "y"}),
        }
    )

    drift = await svc.policy_drift()

    # Only sandboxes present on ≥2 gateways appear.
    assert [d["sandbox"] for d in drift] == ["agent-a"]
    assert drift[0]["drifted"] is True
    assert drift[0]["hashes"] == {"gw1": "h1", "gw2": "h2"}


async def test_policy_drift_in_sync() -> None:
    svc = _fleet(
        {
            "gw1": _make_client(["agent-a"], {"agent-a": "same"}),
            "gw2": _make_client(["agent-a"], {"agent-a": "same"}),
        }
    )

    drift = await svc.policy_drift()
    assert drift[0]["drifted"] is False


async def test_sync_policy_pushes_content_to_targets() -> None:
    source_policy = {
        "status": "loaded",
        "version": 7,
        "network_policies": {"pypi": {"name": "pypi"}},
    }
    gw1 = _make_client(["agent-a"], {"agent-a": "h1"}, policy=source_policy)
    gw2 = _make_client(["agent-a"], {"agent-a": "h2"}, policy={"network_policies": {}})
    svc = _fleet({"gw1": gw1, "gw2": gw2})

    result = await svc.sync_policy(source_gateway="gw1", sandbox="agent-a", targets=["gw2"])

    assert result == {"synced": ["gw2"], "errors": {}}
    gw2.policies.update.assert_called_once()
    pushed_proto = gw2.policies.update.call_args[0][1]
    assert "pypi" in pushed_proto.network_policies


async def test_sync_policy_rejects_source_in_targets() -> None:
    svc = _fleet({"gw1": _make_client([], {})})
    with pytest.raises(ValueError, match="source gateway"):
        await svc.sync_policy(source_gateway="gw1", sandbox="x", targets=["gw1", "gw2"])


async def test_sync_policy_reports_per_target_errors() -> None:
    source_policy = {"network_policies": {"r": {"name": "r"}}}
    gw1 = _make_client(["agent-a"], {}, policy=source_policy)
    svc = _fleet({"gw1": gw1})

    result = await svc.sync_policy(
        source_gateway="gw1", sandbox="agent-a", targets=["gw-dead"]
    )

    assert result["synced"] == []
    assert "gw-dead" in result["errors"]


async def test_sync_policy_requires_policy_content() -> None:
    gw1 = _make_client(["agent-a"], {}, policy={"status": "loaded"})
    svc = _fleet({"gw1": gw1, "gw2": _make_client([], {})})

    with pytest.raises(ValueError, match="no readable policy"):
        await svc.sync_policy(source_gateway="gw1", sandbox="agent-a", targets=["gw2"])


# ─── Routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_fleet_routes_smoke(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "viewer@test.com", "password": "viewerpass1"}
        )
        assert resp.status_code == 200
        resp = await client.get("/api/fleet/overview")
        assert resp.status_code == 200
        assert resp.json() == {"gateways": []}
        resp = await client.get("/api/fleet/policy-drift")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}
        # Viewer must not sync policies.
        resp = await client.post(
            "/api/fleet/policy-sync",
            json={"source_gateway": "a", "sandbox": "s", "targets": ["b"]},
        )
        assert resp.status_code == 403
