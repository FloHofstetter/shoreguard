"""Tests for the gateway runtime tag (M30 libkrun awareness)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.gateway_runtime import (
    GATEWAY_RUNTIME_DOCKER,
    GATEWAY_RUNTIME_KUBERNETES,
    GATEWAY_RUNTIME_LIBKRUN,
    KNOWN_RUNTIMES,
    get_runtime,
    validate_runtime,
)

# ---------------------------------------------------------------------------
# gateway_runtime module (pure-function unit tests)
# ---------------------------------------------------------------------------


class TestRuntimeConstants:
    def test_known_runtimes_exact_set(self):
        assert KNOWN_RUNTIMES == frozenset(
            {
                GATEWAY_RUNTIME_DOCKER,
                GATEWAY_RUNTIME_KUBERNETES,
                GATEWAY_RUNTIME_LIBKRUN,
            }
        )

    def test_libkrun_is_lowercase(self):
        assert GATEWAY_RUNTIME_LIBKRUN == "libkrun"


class TestGetRuntime:
    def test_none_metadata(self):
        assert get_runtime(None) is None

    def test_empty_metadata(self):
        assert get_runtime({}) is None

    def test_missing_runtime_key(self):
        assert get_runtime({"other": "x"}) is None

    def test_empty_runtime_value(self):
        assert get_runtime({"runtime": ""}) is None

    def test_non_string_runtime_value(self):
        assert get_runtime({"runtime": 42}) is None

    def test_present_runtime(self):
        assert get_runtime({"runtime": "libkrun"}) == "libkrun"


class TestValidateRuntime:
    @pytest.mark.parametrize("good", ["docker", "kubernetes", "libkrun"])
    def test_accepts_known(self, good):
        assert validate_runtime(good) == good

    @pytest.mark.parametrize("mixed", ["Docker", "LIBKRUN", "KuBeRnEtEs"])
    def test_normalises_to_lowercase(self, mixed):
        assert validate_runtime(mixed) == mixed.lower()

    @pytest.mark.parametrize("bad", ["krun", "vm", "podman", "libKrunX"])
    def test_rejects_unknown(self, bad):
        with pytest.raises(ValueError, match="not recognised"):
            validate_runtime(bad)

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="non-empty string"):
            validate_runtime("")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="non-empty string"):
            validate_runtime(123)


# ---------------------------------------------------------------------------
# API integration tests for the /register + /list runtime surface
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_gw_svc():
    with patch("shoreguard.services.gateway.gateway_service") as mock:
        yield mock


@pytest.fixture
async def gw_client():
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_register_accepts_libkrun_runtime(gw_client, mock_gw_svc):
    mock_gw_svc.register.return_value = {
        "name": "vm-gw",
        "endpoint": "8.8.8.8:30051",
        "metadata": {"runtime": "libkrun"},
        "runtime": "libkrun",
        "connected": False,
    }
    resp = await gw_client.post(
        "/api/gateway/register",
        json={
            "name": "vm-gw",
            "endpoint": "8.8.8.8:30051",
            "auth_mode": "insecure",
            "metadata": {"runtime": "libkrun"},
        },
    )
    assert resp.status_code == 201
    call = mock_gw_svc.register.call_args
    assert call.kwargs["metadata"] == {"runtime": "libkrun"}
    assert resp.json()["runtime"] == "libkrun"


async def test_register_normalises_runtime_case(gw_client, mock_gw_svc):
    mock_gw_svc.register.return_value = {"name": "vm-gw", "connected": False}
    resp = await gw_client.post(
        "/api/gateway/register",
        json={
            "name": "vm-gw",
            "endpoint": "8.8.8.8:30051",
            "auth_mode": "insecure",
            "metadata": {"runtime": "LIBKRUN"},
        },
    )
    assert resp.status_code == 201
    # The canonical lowercase form is what gets persisted.
    assert mock_gw_svc.register.call_args.kwargs["metadata"] == {"runtime": "libkrun"}


async def test_register_rejects_unknown_runtime(gw_client, mock_gw_svc):
    resp = await gw_client.post(
        "/api/gateway/register",
        json={
            "name": "vm-gw",
            "endpoint": "8.8.8.8:30051",
            "auth_mode": "insecure",
            "metadata": {"runtime": "bogus"},
        },
    )
    assert resp.status_code == 422  # pydantic field validator
    mock_gw_svc.register.assert_not_called()


async def test_register_preserves_other_metadata(gw_client, mock_gw_svc):
    mock_gw_svc.register.return_value = {"name": "vm-gw", "connected": False}
    resp = await gw_client.post(
        "/api/gateway/register",
        json={
            "name": "vm-gw",
            "endpoint": "8.8.8.8:30051",
            "auth_mode": "insecure",
            "metadata": {"runtime": "libkrun", "region": "eu-central-1", "note": "x"},
        },
    )
    assert resp.status_code == 201
    persisted = mock_gw_svc.register.call_args.kwargs["metadata"]
    assert persisted == {
        "runtime": "libkrun",
        "region": "eu-central-1",
        "note": "x",
    }


async def test_list_filters_by_runtime(gw_client, mock_gw_svc):
    mock_gw_svc.list_all.return_value = [
        {"name": "docker-gw", "metadata": {"runtime": "docker"}},
        {"name": "vm-gw", "metadata": {"runtime": "libkrun"}},
        {"name": "k8s-gw", "metadata": {"runtime": "kubernetes"}},
        {"name": "untagged", "metadata": {}},
    ]
    resp = await gw_client.get("/api/gateway/list?runtime=libkrun")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [gw["name"] for gw in items] == ["vm-gw"]
    assert resp.json()["total"] == 1


async def test_list_rejects_unknown_runtime_filter(gw_client, mock_gw_svc):
    resp = await gw_client.get("/api/gateway/list?runtime=bogus")
    assert resp.status_code == 400
    assert "not recognised" in resp.json()["detail"]
    mock_gw_svc.list_all.assert_not_called()


async def test_list_runtime_filter_is_case_insensitive(gw_client, mock_gw_svc):
    mock_gw_svc.list_all.return_value = [
        {"name": "vm-gw", "metadata": {"runtime": "libkrun"}},
    ]
    resp = await gw_client.get("/api/gateway/list?runtime=LIBKRUN")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


async def test_list_no_runtime_filter_returns_all(gw_client, mock_gw_svc):
    mock_gw_svc.list_all.return_value = [
        {"name": "docker-gw", "metadata": {"runtime": "docker"}},
        {"name": "vm-gw", "metadata": {"runtime": "libkrun"}},
    ]
    resp = await gw_client.get("/api/gateway/list")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2
