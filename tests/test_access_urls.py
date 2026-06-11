"""Tests for the access-urls service and the /api/system/access-urls route."""

from __future__ import annotations

import json
import subprocess

import pytest

from shoreguard.services import access_urls as mod

_IP_ADDR_OUTPUT = json.dumps(
    [
        {
            "ifname": "lo",
            "addr_info": [
                {"family": "inet", "local": "127.0.0.1", "scope": "host"},
                {"family": "inet6", "local": "::1", "scope": "host"},
            ],
        },
        {
            "ifname": "enp5s0",
            "addr_info": [
                {"family": "inet", "local": "192.168.178.28", "scope": "global"},
                {"family": "inet6", "local": "fe80::1", "scope": "link"},
            ],
        },
        {
            "ifname": "tailscale0",
            "addr_info": [
                {"family": "inet", "local": "100.101.102.103", "scope": "global"},
            ],
        },
    ]
)


def _fake_ip_run(output: str):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=output, stderr="")

    return run


def test_ip_command_addresses_filters_scope_and_family(monkeypatch) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/ip")
    monkeypatch.setattr(mod.subprocess, "run", _fake_ip_run(_IP_ADDR_OUTPUT))

    assert mod._ip_command_addresses() == ["192.168.178.28", "100.101.102.103"]


def test_ip_command_addresses_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    assert mod._ip_command_addresses() == []


def test_host_addresses_orders_lan_before_tailnet(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "_ip_command_addresses",
        lambda: ["100.101.102.103", "192.168.178.28", "192.168.178.28", "127.0.0.1"],
    )

    assert mod.host_addresses() == ["192.168.178.28", "100.101.102.103"]


def test_host_addresses_falls_back_to_default_route(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_ip_command_addresses", lambda: [])
    monkeypatch.setattr(mod, "_default_route_address", lambda: "10.0.0.5")

    assert mod.host_addresses() == ["10.0.0.5"]


def test_host_addresses_empty_when_offline(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_ip_command_addresses", lambda: [])
    monkeypatch.setattr(mod, "_default_route_address", lambda: None)

    assert mod.host_addresses() == []


@pytest.mark.parametrize(
    ("bind_host", "loopback_only", "expected_urls"),
    [
        ("127.0.0.1", True, ["http://192.168.178.28:8888/"]),
        ("0.0.0.0", False, ["http://192.168.178.28:8888/"]),
        ("192.168.178.28", False, ["http://192.168.178.28:8888/"]),
        ("::", False, ["http://192.168.178.28:8888/"]),
    ],
)
def test_access_urls_bind_modes(monkeypatch, bind_host, loopback_only, expected_urls) -> None:
    from shoreguard.settings import reset_settings

    monkeypatch.setenv("SHOREGUARD_HOST", bind_host)
    monkeypatch.setenv("SHOREGUARD_PORT", "8888")
    reset_settings()
    monkeypatch.setattr(mod, "host_addresses", lambda: ["192.168.178.28"])

    result = mod.access_urls()

    assert result["bind_host"] == bind_host
    assert result["port"] == 8888
    assert result["loopback_only"] is loopback_only
    assert result["lan_urls"] == expected_urls


def test_access_urls_brackets_ipv6_bind(monkeypatch) -> None:
    from shoreguard.settings import reset_settings

    monkeypatch.setenv("SHOREGUARD_HOST", "fd21:2365:e6ff::1")
    monkeypatch.setenv("SHOREGUARD_PORT", "8888")
    reset_settings()

    result = mod.access_urls()

    assert result["loopback_only"] is False
    assert result["lan_urls"] == ["http://[fd21:2365:e6ff::1]:8888/"]


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_access_urls_route(db, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.auth import create_user

    monkeypatch.setattr(mod, "host_addresses", lambda: ["192.168.178.28"])
    await create_user("viewer@test.com", "viewerpass1", "viewer")
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "viewer@test.com", "password": "viewerpass1"},
        )
        assert resp.status_code == 200
        resp = await client.get("/api/system/access-urls")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"bind_host", "port", "loopback_only", "lan_urls"}
        assert all(u.startswith("http://") for u in data["lan_urls"])


async def test_access_urls_route_requires_auth(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/system/access-urls")
        assert resp.status_code == 401
