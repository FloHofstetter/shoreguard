"""Tests for api/main.py — error handlers, page routes, WebSocket."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import grpc
import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.exceptions import (
    GatewayNotConnectedError,
    NotFoundError,
    PolicyError,
    SandboxError,
)


@pytest.fixture
def mock_client():
    from shoreguard.client import ShoreGuardClient

    client = MagicMock(spec=ShoreGuardClient)
    client.sandboxes = MagicMock()
    client.policies = MagicMock()
    client.providers = MagicMock()
    client.approvals = MagicMock()
    return client


@pytest.fixture
async def api_client(mock_client):
    from shoreguard.api.deps import get_client
    from shoreguard.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()


GW = "test"


# ─── 3A: Error Handlers ──────────────────────────────────────────────────────


class _FakeRpcError(grpc.RpcError, grpc.Call):
    """Fake gRPC error with code() and details().

    Must also inherit from grpc.Call so isinstance checks work through
    Starlette's exception handling middleware.
    """

    def __init__(self, code, details="gRPC error"):
        super().__init__()
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details

    # grpc.Call requires these methods
    def initial_metadata(self):
        return {}

    def trailing_metadata(self):
        return {}

    def is_active(self):
        return False

    def cancelled(self):
        return False

    def time_remaining(self):
        return None

    def add_callback(self, callback):
        pass


async def test_gateway_not_connected_returns_503(api_client, mock_client):
    mock_client.sandboxes.list.side_effect = GatewayNotConnectedError("not connected")
    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes")
    assert resp.status_code == 503
    assert "not connected" in resp.json()["detail"]


async def test_not_found_returns_404(api_client, mock_client):
    mock_client.sandboxes.get.side_effect = NotFoundError("sandbox not found")
    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/missing")
    assert resp.status_code == 404


async def test_policy_error_returns_400(api_client, mock_client):
    mock_client.policies.get.side_effect = PolicyError("policy corrupt")
    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/sb1/policy")
    assert resp.status_code == 400


async def test_sandbox_error_returns_409(api_client, mock_client):
    mock_client.sandboxes.get.side_effect = SandboxError("sandbox crashed")
    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/broken")
    assert resp.status_code == 409


async def test_grpc_deadline_exceeded_returns_504(api_client, mock_client):
    mock_client.sandboxes.list.side_effect = _FakeRpcError(
        grpc.StatusCode.DEADLINE_EXCEEDED, "deadline exceeded"
    )
    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes")
    assert resp.status_code == 504


async def test_timeout_returns_504(api_client, mock_client):
    mock_client.sandboxes.list.side_effect = TimeoutError("timed out")
    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes")
    assert resp.status_code == 504


async def test_grpc_error_handler_maps_status_codes():
    """Verify the gRPC status code mapping dict is correct."""
    from shoreguard.api.errors import _GRPC_STATUS_MAP

    assert _GRPC_STATUS_MAP[grpc.StatusCode.NOT_FOUND] == 404
    assert _GRPC_STATUS_MAP[grpc.StatusCode.ALREADY_EXISTS] == 409
    assert _GRPC_STATUS_MAP[grpc.StatusCode.UNAVAILABLE] == 503
    assert _GRPC_STATUS_MAP[grpc.StatusCode.INVALID_ARGUMENT] == 400
    assert _GRPC_STATUS_MAP[grpc.StatusCode.PERMISSION_DENIED] == 403
    assert _GRPC_STATUS_MAP[grpc.StatusCode.UNAUTHENTICATED] == 401
    assert _GRPC_STATUS_MAP[grpc.StatusCode.UNIMPLEMENTED] == 501
    assert _GRPC_STATUS_MAP[grpc.StatusCode.DEADLINE_EXCEEDED] == 504


async def test_grpc_unimplemented_returns_501(api_client, mock_client):
    """gRPC UNIMPLEMENTED returns 501 with feature and upgrade_required fields."""
    mock_client.policies.get.side_effect = _FakeRpcError(
        grpc.StatusCode.UNIMPLEMENTED, "Method not implemented"
    )
    resp = await api_client.get(f"/api/gateways/{GW}/sandboxes/sb1/policy")
    assert resp.status_code == 501
    data = resp.json()
    assert data["upgrade_required"] is True
    assert "feature" in data


async def test_domain_error_status_map():
    """Verify domain exception → HTTP status mapping."""
    from shoreguard.api.errors import _DOMAIN_STATUS_MAP
    from shoreguard.exceptions import FeatureNotAvailableError

    assert _DOMAIN_STATUS_MAP[GatewayNotConnectedError] == 503
    assert _DOMAIN_STATUS_MAP[NotFoundError] == 404
    assert _DOMAIN_STATUS_MAP[PolicyError] == 400
    assert _DOMAIN_STATUS_MAP[SandboxError] == 409
    assert _DOMAIN_STATUS_MAP[FeatureNotAvailableError] == 501


def test_detect_feature_from_path_policy():
    from shoreguard.api.errors import _detect_feature_from_path

    assert "policy" in _detect_feature_from_path("/api/gateways/gw/sandboxes/sb/policy").lower()


def test_detect_feature_from_path_approvals():
    from shoreguard.api.errors import _detect_feature_from_path

    result = _detect_feature_from_path("/api/gateways/gw/sandboxes/sb/approvals")
    assert "approval" in result.lower()


def test_detect_feature_from_path_inference():
    from shoreguard.api.errors import _detect_feature_from_path

    assert "inference" in _detect_feature_from_path("/api/gateways/gw/inference").lower()


def test_detect_feature_from_path_unknown():
    from shoreguard.api.errors import _detect_feature_from_path

    assert _detect_feature_from_path("/api/gateways/gw/sandboxes") == "This operation"


# ─── 3B: Page Routes ─────────────────────────────────────────────────────────


@pytest.fixture
async def page_client():
    """Client for page routes — no gateway dependency override needed."""
    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def test_root_redirects(page_client):
    resp = await page_client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/gateways"


async def test_gateways_page(page_client):
    resp = await page_client.get("/gateways")
    assert resp.status_code == 200
    assert "Gateways" in resp.text


async def test_gateway_detail_page(page_client):
    resp = await page_client.get("/gateways/mygw")
    assert resp.status_code == 200


async def test_sandboxes_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes")
    assert resp.status_code == 200
    assert "Sandboxes" in resp.text


async def test_sandbox_detail_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1")
    assert resp.status_code == 200


async def test_sandbox_policy_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/policy")
    assert resp.status_code == 200


async def test_sandbox_approvals_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/approvals")
    assert resp.status_code == 200


async def test_sandbox_logs_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/logs")
    assert resp.status_code == 200


async def test_sandbox_terminal_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/terminal")
    assert resp.status_code == 200


async def test_sandbox_network_policies_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/network-policies")
    assert resp.status_code == 200


async def test_sandbox_filesystem_policy_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/filesystem-policy")
    assert resp.status_code == 200


async def test_sandbox_process_policy_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/process-policy")
    assert resp.status_code == 200


async def test_sandbox_apply_preset_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/apply-preset")
    assert resp.status_code == 200


async def test_sandbox_rule_detail_page(page_client):
    resp = await page_client.get("/gateways/mygw/sandboxes/sb1/rules/pypi")
    assert resp.status_code == 200


async def test_providers_page(page_client):
    resp = await page_client.get("/gateways/mygw/providers")
    assert resp.status_code == 200


async def test_wizard_page(page_client):
    resp = await page_client.get("/gateways/mygw/wizard")
    assert resp.status_code == 200


async def test_unknown_subpage_404(page_client):
    resp = await page_client.get("/gateways/mygw/nonexistent")
    assert resp.status_code == 404


async def test_policies_page(page_client):
    resp = await page_client.get("/policies")
    assert resp.status_code == 200


async def test_preset_detail_page(page_client):
    resp = await page_client.get("/policies/pypi")
    assert resp.status_code == 200


# ─── 3C: WebSocket ───────────────────────────────────────────────────────────


def test_ws_gateway_not_connected():
    """WebSocket sends error JSON when gateway is not connected."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    with patch(
        "shoreguard.api.main.get_client",
        side_effect=GatewayNotConnectedError("not connected"),
    ):
        client = TestClient(app)
        with client.websocket_connect("/ws/test-gw/sb1") as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "not connected" in data["data"]["message"]


def test_ws_streams_sandbox_events():
    """WebSocket streams sandbox watch events to the client."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "test-sb"}
    # watch() returns an iterable of events
    mock_client.sandboxes.watch.return_value = iter(
        [
            {"type": "status", "sandbox": "test-sb", "phase": "ready"},
            {"type": "log", "sandbox": "test-sb", "message": "hello"},
        ]
    )

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/test-gw/test-sb") as ws:
            msg1 = ws.receive_json()
            assert msg1["type"] == "status"
            msg2 = ws.receive_json()
            assert msg2["type"] == "log"


def test_ws_handles_grpc_stream_error():
    """WebSocket sends error event when gRPC watch stream fails."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "test-sb"}

    # Create a fake gRPC error
    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "stream died"

    mock_client.sandboxes.watch.side_effect = _FakeRpcError()

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/test-gw/test-sb") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "not reachable" in msg.get("data", {}).get("message", "")


def test_ws_client_disconnect():
    """WebSocket cleanup handles client disconnect gracefully."""
    import time

    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "test-sb"}

    # watch() returns a slow generator that yields forever
    def slow_watch(**kwargs):
        while True:
            time.sleep(0.5)
            yield {"type": "heartbeat"}

    mock_client.sandboxes.watch.return_value = slow_watch()

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/test-gw/test-sb") as ws:
            # Receive at least one event then disconnect
            msg = ws.receive_json(mode="text")
            assert msg["type"] == "heartbeat"
        # Exiting the context manager disconnects - no crash expected


# ─── 3D: CLI (Typer) ─────────────────────────────────────────────────────────


def _cli_runner():
    from typer.testing import CliRunner

    return CliRunner()


def test_cli_defaults():
    from shoreguard.api.cli import cli

    runner = _cli_runner()
    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(cli, [])
        assert result.exit_code == 0
        assert mock_run.call_args[1]["host"] == "0.0.0.0"
        assert mock_run.call_args[1]["port"] == 8888
        assert mock_run.call_args[1]["reload"] is True


def test_cli_all_flags():
    from shoreguard.api.cli import cli

    runner = _cli_runner()
    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(
            cli,
            ["--host", "127.0.0.1", "--port", "9000", "--log-level", "debug", "--no-reload"],
        )
        assert result.exit_code == 0
        assert mock_run.call_args[1]["host"] == "127.0.0.1"
        assert mock_run.call_args[1]["port"] == 9000
        assert mock_run.call_args[1]["log_level"] == "debug"
        assert mock_run.call_args[1]["reload"] is False


def test_cli_version():
    from shoreguard.api.cli import cli

    runner = _cli_runner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "shoreguard" in result.output


def test_cli_env_fallback():
    from shoreguard.api.cli import cli

    runner = _cli_runner()
    with patch.dict("os.environ", {"SHOREGUARD_HOST": "10.0.0.1", "SHOREGUARD_PORT": "7777"}):
        with patch("uvicorn.run") as mock_run:
            result = runner.invoke(cli, [])
            assert result.exit_code == 0
            assert mock_run.call_args[1]["host"] == "10.0.0.1"
            assert mock_run.call_args[1]["port"] == 7777


def test_cli_overrides_env():
    from shoreguard.api.cli import cli

    runner = _cli_runner()
    with patch.dict("os.environ", {"SHOREGUARD_HOST": "10.0.0.1"}):
        with patch("uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["--host", "127.0.0.1"])
            assert result.exit_code == 0
            assert mock_run.call_args[1]["host"] == "127.0.0.1"


# ─── 3E: Frontend Resolution ─────────────────────────────────────────────────


def test_resolve_frontend_dir_dev():
    from shoreguard.api.pages import _resolve_frontend_dir

    result = _resolve_frontend_dir()
    assert result.is_dir()
    assert (result / "templates").is_dir()
    assert (result / "js").is_dir()


def test_resolve_frontend_dir_missing():
    from shoreguard.api.pages import _resolve_frontend_dir

    with patch("pathlib.Path.is_dir", return_value=False):
        with pytest.raises(FileNotFoundError, match="Frontend directory not found"):
            _resolve_frontend_dir()
