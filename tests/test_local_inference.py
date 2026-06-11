"""Tests for local inference server detection (solo-dev on-ramp)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.services.local_inference import _extract_models, detect_local_inference

OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
VLLM_MODELS_URL = "http://127.0.0.1:8000/v1/models"


def _client_with(responses):
    """Patch httpx.Client so each probe URL maps to a response or exception."""

    def handler(request):
        result = responses.get(str(request.url))
        if result is None:
            raise httpx.ConnectError("connection refused")
        return result

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(**kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return real_client(transport=transport)

    return patch("shoreguard.services.local_inference.httpx.Client", side_effect=factory)


class TestDetectLocalInference:
    def test_nothing_running_returns_empty(self):
        with _client_with({}):
            assert detect_local_inference() == []

    def test_ollama_detected_with_models(self):
        responses = {
            OLLAMA_TAGS_URL: httpx.Response(
                200, json={"models": [{"name": "llama3:8b"}, {"name": "qwen3:4b"}]}
            )
        }
        with _client_with(responses):
            detected = detect_local_inference()
        assert len(detected) == 1
        entry = detected[0]
        assert entry["kind"] == "ollama"
        assert entry["base_url"] == "http://127.0.0.1:11434/v1"
        assert entry["provider_type"] == "openai"
        assert entry["models"] == ["llama3:8b", "qwen3:4b"]

    def test_openai_models_shape_detected(self):
        responses = {
            VLLM_MODELS_URL: httpx.Response(
                200, json={"object": "list", "data": [{"id": "meta/llama-3.1-8b"}]}
            )
        }
        with _client_with(responses):
            detected = detect_local_inference()
        assert len(detected) == 1
        assert detected[0]["label"] == "vLLM / NIM"
        assert detected[0]["models"] == ["meta/llama-3.1-8b"]

    def test_multiple_servers_detected(self):
        responses = {
            OLLAMA_TAGS_URL: httpx.Response(200, json={"models": []}),
            VLLM_MODELS_URL: httpx.Response(200, json={"data": []}),
        }
        with _client_with(responses):
            detected = detect_local_inference()
        assert [d["kind"] for d in detected] == ["ollama", "openai-compatible"]

    def test_non_json_port_not_misreported(self):
        """A random web app on a known port must not be reported as an LLM."""
        responses = {OLLAMA_TAGS_URL: httpx.Response(200, text="<html>hi</html>")}
        with _client_with(responses):
            assert detect_local_inference() == []

    def test_http_error_not_reported(self):
        responses = {VLLM_MODELS_URL: httpx.Response(500, json={"error": "boom"})}
        with _client_with(responses):
            assert detect_local_inference() == []


class TestExtractModels:
    def test_caps_model_count(self):
        payload = {"data": [{"id": f"m{i}"} for i in range(20)]}
        assert len(_extract_models(payload)) == 8

    def test_tolerates_garbage(self):
        assert _extract_models("nope") == []
        assert _extract_models({"data": "nope"}) == []
        assert _extract_models({"data": [{"weird": 1}, 42]}) == []


# ─── Route ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def api_client():
    from shoreguard.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_route_empty_outside_local_mode(api_client, monkeypatch):
    from shoreguard.settings import reset_settings

    monkeypatch.delenv("SHOREGUARD_LOCAL_MODE", raising=False)
    reset_settings()
    resp = await api_client.get("/api/gateway/local-inference")
    assert resp.status_code == 200
    assert resp.json() == {"local_mode": False, "detected": []}


async def test_route_probes_in_local_mode(api_client, monkeypatch):
    from shoreguard.settings import reset_settings

    monkeypatch.setenv("SHOREGUARD_LOCAL_MODE", "true")
    reset_settings()
    fake = [
        {
            "kind": "ollama",
            "label": "Ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "provider_type": "openai",
            "suggested_name": "ollama-local",
            "models": ["llama3:8b"],
        }
    ]
    with patch(
        "shoreguard.services.local_inference.detect_local_inference", return_value=fake
    ) as mock_detect:
        resp = await api_client.get("/api/gateway/local-inference")
    assert resp.status_code == 200
    body = resp.json()
    assert body["local_mode"] is True
    assert body["detected"] == fake
    mock_detect.assert_called_once()


# ─── probe_endpoint (operator-supplied LAN URL) ──────────────────────────────


class TestProbeEndpoint:
    def test_rejects_bad_scheme(self):
        from shoreguard.services.local_inference import probe_endpoint

        result = probe_endpoint("ftp://192.168.1.10/v1")
        assert result["ok"] is False
        assert "scheme" in result["error"]

    def test_rejects_public_address(self):
        from shoreguard.services.local_inference import probe_endpoint

        result = probe_endpoint("http://8.8.8.8/v1")
        assert result["ok"] is False
        assert "private" in result["error"]

    def test_probes_openai_models(self):
        from shoreguard.services.local_inference import probe_endpoint

        responses = {
            "http://192.168.1.20:11434/v1/models": httpx.Response(
                200, json={"data": [{"id": "llama3:8b"}]}
            )
        }
        with _client_with(responses):
            result = probe_endpoint("http://192.168.1.20:11434/v1")
        assert result == {"ok": True, "models": ["llama3:8b"], "error": None}

    def test_falls_back_to_ollama_tags(self):
        from shoreguard.services.local_inference import probe_endpoint

        responses = {
            "http://192.168.1.20:11434/api/tags": httpx.Response(
                200, json={"models": [{"name": "qwen3:4b"}]}
            )
        }
        with _client_with(responses):
            result = probe_endpoint("http://192.168.1.20:11434/v1")
        assert result["ok"] is True
        assert result["models"] == ["qwen3:4b"]

    def test_unreachable_endpoint_reports_error(self):
        from shoreguard.services.local_inference import probe_endpoint

        with _client_with({}):
            result = probe_endpoint("http://192.168.1.20:9999/v1")
        assert result["ok"] is False
        assert result["models"] == []
        assert result["error"]

    def test_localhost_allowed(self):
        from shoreguard.services.local_inference import probe_endpoint

        responses = {"http://localhost:1234/v1/models": httpx.Response(200, json={"data": []})}
        with _client_with(responses):
            result = probe_endpoint("http://localhost:1234/v1")
        assert result["ok"] is True


@pytest.fixture
def auth_db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


async def test_probe_route_requires_operator(auth_db):
    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user("viewer@test.com", "viewerpass1", "viewer")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "viewer@test.com", "password": "viewerpass1"}
        )
        assert resp.status_code == 200
        resp = await client.post(
            "/api/system/probe-inference", json={"base_url": "http://192.168.1.20/v1"}
        )
        assert resp.status_code == 403


async def test_probe_route_operator_ok(auth_db):
    from shoreguard.api.auth import create_user
    from shoreguard.api.main import app

    await create_user("op@test.com", "operatorpass1", "operator")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "op@test.com", "password": "operatorpass1"}
        )
        assert resp.status_code == 200
        responses = {
            "http://192.168.1.20:8000/v1/models": httpx.Response(
                200, json={"data": [{"id": "nim/llama"}]}
            )
        }
        with _client_with(responses):
            resp = await client.post(
                "/api/system/probe-inference",
                json={"base_url": "http://192.168.1.20:8000/v1"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "models": ["nim/llama"], "error": None}
