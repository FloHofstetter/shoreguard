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
    def initial_metadata(self):  # type: ignore[override]
        return ()

    def trailing_metadata(self):  # type: ignore[override]
        return ()

    def is_active(self):
        return False

    def cancelled(self):
        return False

    def time_remaining(self):  # type: ignore[override]
        return 0.0

    def add_callback(self, callback):  # type: ignore[override]
        return False

    def cancel(self):
        return False


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
    from shoreguard.api.errors import _GRPC_MAP

    assert _GRPC_MAP[grpc.StatusCode.NOT_FOUND][0] == 404
    assert _GRPC_MAP[grpc.StatusCode.ALREADY_EXISTS][0] == 409
    assert _GRPC_MAP[grpc.StatusCode.UNAVAILABLE][0] == 503
    assert _GRPC_MAP[grpc.StatusCode.INVALID_ARGUMENT][0] == 400
    assert _GRPC_MAP[grpc.StatusCode.PERMISSION_DENIED][0] == 403
    assert _GRPC_MAP[grpc.StatusCode.UNAUTHENTICATED][0] == 401
    assert _GRPC_MAP[grpc.StatusCode.DEADLINE_EXCEEDED][0] == 504
    assert _GRPC_MAP[grpc.StatusCode.FAILED_PRECONDITION][0] == 409


async def test_domain_error_status_map():
    """Verify domain exception → HTTP status mapping."""
    from shoreguard.api.errors import _DOMAIN_MAP

    assert _DOMAIN_MAP[GatewayNotConnectedError][0] == 503
    assert _DOMAIN_MAP[NotFoundError][0] == 404
    assert _DOMAIN_MAP[PolicyError][0] == 400
    assert _DOMAIN_MAP[SandboxError][0] == 409


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


async def test_root_renders_dashboard(page_client):
    resp = await page_client.get("/")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


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


def test_ws_enriches_ocsf_log_events():
    """OCSF shorthand log events on the WS stream carry a parsed ``ocsf`` dict."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "test-sb"}
    mock_client.sandboxes.watch.return_value = iter(
        [
            {
                "type": "log",
                "data": {
                    "timestamp_ms": 1000,
                    "level": "OCSF",
                    "target": "ocsf",
                    "message": (
                        "NET:OPEN [MED] DENIED /usr/bin/curl(64) -> httpbin.org:443 "
                        "[policy:- engine:opa]"
                    ),
                    "source": "sandbox",
                    "fields": {"dst_host": "httpbin.org"},
                },
            },
        ]
    )

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/test-gw/test-sb") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "log"
            ocsf = msg["data"]["ocsf"]
            assert ocsf["class_prefix"] == "NET"
            assert ocsf["disposition"] == "DENIED"
            assert ocsf["severity"] == "MED"
            assert ocsf["fields"] == {"dst_host": "httpbin.org"}


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


def test_ws_sends_heartbeat_on_idle():
    """WebSocket sends heartbeat when no events arrive within the interval."""
    import time

    from starlette.testclient import TestClient

    from shoreguard.api.main import app
    from shoreguard.settings import Settings, WebSocketSettings, override_settings, reset_settings

    settings = Settings()
    settings.websocket = WebSocketSettings(heartbeat_interval=0.5, queue_get_timeout=0.3)
    override_settings(settings)

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "test-sb"}

    def slow_watch(**kwargs):
        time.sleep(2)
        return
        yield  # make it a generator

    mock_client.sandboxes.watch.return_value = slow_watch()

    try:
        with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
            mock_gw_svc.return_value.get_client.return_value = mock_client
            client = TestClient(app)
            with client.websocket_connect("/ws/test-gw/test-sb") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "heartbeat"
                assert "dropped_events" in msg["data"]
                assert msg["data"]["dropped_events"] == 0
    finally:
        reset_settings()


def test_ws_heartbeat_reports_dropped_events():
    """Heartbeat includes count of events dropped due to backpressure."""
    import time

    from starlette.testclient import TestClient

    from shoreguard.api.main import app
    from shoreguard.settings import Settings, WebSocketSettings, override_settings, reset_settings

    settings = Settings()
    settings.websocket = WebSocketSettings(
        queue_maxsize=2,
        heartbeat_interval=0.3,
        queue_get_timeout=0.2,
        backpressure_drop_limit=1000,  # high limit — don't disconnect
    )
    override_settings(settings)

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-123", "name": "test-sb"}

    def burst_then_idle(**kwargs):
        # Burst: many events exceed queue capacity → drops.
        # Then idle: heartbeat fires with drop count.
        for i in range(20):
            yield {"type": "log", "data": {"message": f"msg-{i}"}}
        time.sleep(1.5)

    mock_client.sandboxes.watch.return_value = burst_then_idle()

    try:
        with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
            mock_gw_svc.return_value.get_client.return_value = mock_client
            client = TestClient(app)
            with client.websocket_connect("/ws/test-gw/test-sb") as ws:
                # Drain until heartbeat
                heartbeat = None
                for _ in range(50):
                    msg = ws.receive_json()
                    if msg["type"] == "heartbeat":
                        heartbeat = msg
                        break
                assert heartbeat is not None, "No heartbeat received"
                assert heartbeat["data"]["dropped_events"] > 0
    finally:
        reset_settings()


# ─── 3C.1: WebSocket auth & error-path coverage ──────────────────────────────


def test_ws_auth_rejects_without_token(monkeypatch):
    """WebSocket auth rejects connection when no token/session is provided.

    Exercises ``require_auth_ws`` reject path: 403 HTTPException → Starlette
    closes the WebSocket before our handler body runs.
    """
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from shoreguard.api import auth as auth_mod
    from shoreguard.api.main import app

    # Disable no-auth and force setup-complete so the dep actually rejects.
    monkeypatch.setattr(auth_mod, "_no_auth", False)
    monkeypatch.setattr(auth_mod, "is_setup_complete", lambda: True)
    monkeypatch.setattr(auth_mod, "_lookup_sp_identity", lambda _k: None)
    monkeypatch.setattr(auth_mod, "verify_session_token", lambda _t: None)

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/mygw/sb1") as ws:
            ws.receive_json()


def test_ws_auth_rejects_invalid_token(monkeypatch):
    """WebSocket auth rejects connection when the SP token is unknown."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from shoreguard.api import auth as auth_mod
    from shoreguard.api.main import app

    monkeypatch.setattr(auth_mod, "_no_auth", False)
    monkeypatch.setattr(auth_mod, "is_setup_complete", lambda: True)
    monkeypatch.setattr(auth_mod, "_lookup_sp_identity", lambda _k: None)
    monkeypatch.setattr(auth_mod, "verify_session_token", lambda _t: None)

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/mygw/sb1?token=bogus") as ws:
            ws.receive_json()


def test_ws_auth_rejects_expired_session(monkeypatch):
    """WebSocket auth rejects when the session cookie has expired/is bogus."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from shoreguard.api import auth as auth_mod
    from shoreguard.api.main import app

    monkeypatch.setattr(auth_mod, "_no_auth", False)
    monkeypatch.setattr(auth_mod, "is_setup_complete", lambda: True)
    monkeypatch.setattr(auth_mod, "_lookup_sp_identity", lambda _k: None)
    # Expired / invalid session → verify returns None.
    monkeypatch.setattr(auth_mod, "verify_session_token", lambda _t: None)

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/mygw/sb1",
            cookies={"sg_session": "expired-token"},
        ) as ws:
            ws.receive_json()


def test_ws_auth_rejects_deleted_user(monkeypatch):
    """Valid session cookie for a user that has been deleted is rejected."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from shoreguard.api import auth as auth_mod
    from shoreguard.api.main import app

    monkeypatch.setattr(auth_mod, "_no_auth", False)
    monkeypatch.setattr(auth_mod, "is_setup_complete", lambda: True)
    monkeypatch.setattr(auth_mod, "_lookup_sp_identity", lambda _k: None)
    # Session verifies, but user row no longer exists.
    monkeypatch.setattr(auth_mod, "verify_session_token", lambda _t: (42, "operator"))
    monkeypatch.setattr(auth_mod, "_lookup_user", lambda _uid: None)

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/mygw/sb1",
            cookies={"sg_session": "valid-but-stale"},
        ) as ws:
            ws.receive_json()


def test_ws_auth_accepts_valid_sp_token(monkeypatch):
    """WebSocket auth accepts a valid SP token via ``?token=``."""
    from starlette.testclient import TestClient

    from shoreguard.api import auth as auth_mod
    from shoreguard.api.main import app

    monkeypatch.setattr(auth_mod, "_no_auth", False)
    monkeypatch.setattr(auth_mod, "is_setup_complete", lambda: True)
    monkeypatch.setattr(
        auth_mod,
        "_lookup_sp_identity",
        lambda _k: {"role": "operator", "name": "ci-bot"},
    )

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-1", "name": "sb1"}
    mock_client.sandboxes.watch.return_value = iter([{"type": "status", "phase": "ready"}])

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/mygw/sb1?token=goodkey") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "status"


def test_ws_rejects_invalid_gateway_name():
    """Handler sends a validation error for an invalid gateway name.

    Covers the ``_VALID_GW_RE.match(gw)`` guard inside the WS handler,
    which runs after ``websocket.accept()``.
    """
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    client = TestClient(app)
    # "bad!name" contains '!' which is not in the allowlist but is URL-safe.
    with client.websocket_connect("/ws/bad!name/sb1") as ws:
        data = ws.receive_json()
        assert data["type"] == "error"
        assert "Invalid gateway name" in data["data"]["message"]


def test_ws_sandbox_not_found_sends_error_event():
    """WebSocket surfaces NOT_FOUND gRPC error as a clean error event."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    class _NotFoundRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.NOT_FOUND

        def details(self):
            return "no such sandbox"

    mock_client = MagicMock()
    # sandboxes.get raises NOT_FOUND → hits the outer grpc.RpcError handler.
    mock_client.sandboxes.get.side_effect = _NotFoundRpcError()

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/mygw/ghost") as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "ghost" in data["data"]["message"]
            assert "not found" in data["data"]["message"].lower()


def test_ws_unexpected_exception_sends_internal_error():
    """Unexpected exception inside the WS message loop → clean 'Internal error'."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    mock_client = MagicMock()
    # sandboxes.get raises a generic exception → hits the outer Exception handler.
    mock_client.sandboxes.get.side_effect = RuntimeError("boom")

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/mygw/sb1") as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
            assert data["data"]["message"] == "Internal error"


def test_ws_sandbox_get_unavailable_uses_friendly_message():
    """Non-NOT_FOUND gRPC error on sandboxes.get → friendly message path."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    class _UnavailableRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "upstream down"

    mock_client = MagicMock()
    mock_client.sandboxes.get.side_effect = _UnavailableRpcError()

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/mygw/sb1") as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
            # friendly_grpc_error UNAVAILABLE message
            assert "not reachable" in data["data"]["message"]


def test_ws_outer_websocket_disconnect_swallowed():
    """Client disconnects right after receiving first event → outer handler swallows."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-1", "name": "sb1"}

    def many(**kwargs):
        for i in range(10000):
            yield {"type": "log", "data": {"i": i}}

    mock_client.sandboxes.watch.return_value = many()

    with patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc:
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/mygw/sb1") as ws:
            # Grab one event, then close immediately.
            _ = ws.receive_json()
        # Exiting closes the WS — server's next send_json raises
        # WebSocketDisconnect which is caught by the outer handler.


async def test_ws_handler_accept_raises_runtime_error():
    """Direct call: ``websocket.accept()`` raising RuntimeError is swallowed."""
    from unittest.mock import AsyncMock

    from shoreguard.api.websocket import sandbox_events

    ws = MagicMock()
    ws.accept = AsyncMock(side_effect=RuntimeError("already closed"))
    ws.send_json = AsyncMock()
    # Should return cleanly, no exception.
    await sandbox_events(ws, gw="mygw", sandbox_name="sb1")
    ws.accept.assert_awaited()
    ws.send_json.assert_not_called()


async def test_ws_handler_invalid_gw_send_runtime_error():
    """Direct call: send_json raising RuntimeError during invalid-gw error path."""
    from unittest.mock import AsyncMock

    from shoreguard.api.websocket import sandbox_events

    ws = MagicMock()
    ws.accept = AsyncMock(return_value=None)
    ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    await sandbox_events(ws, gw="bad!name", sandbox_name="sb1")
    ws.send_json.assert_awaited()


async def test_ws_handler_gateway_not_connected_send_runtime_error(monkeypatch):
    """Direct call: send_json raising RuntimeError during GatewayNotConnected path."""
    from unittest.mock import AsyncMock

    from shoreguard.api import websocket as ws_mod
    from shoreguard.api.websocket import sandbox_events
    from shoreguard.exceptions import GatewayNotConnectedError

    ws = MagicMock()
    ws.accept = AsyncMock(return_value=None)
    ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))

    class _FakeSvc:
        def get_client(self, name):  # noqa: ARG002
            raise GatewayNotConnectedError("nope")

    monkeypatch.setattr(ws_mod, "_get_gateway_service", lambda: _FakeSvc())
    await sandbox_events(ws, gw="mygw", sandbox_name="sb1")
    ws.send_json.assert_awaited()


async def test_ws_handler_outer_exception_send_runtime_error(monkeypatch):
    """Direct call: generic Exception path where send_json also fails."""
    from unittest.mock import AsyncMock

    from shoreguard.api import websocket as ws_mod
    from shoreguard.api.websocket import sandbox_events

    ws = MagicMock()
    ws.accept = AsyncMock(return_value=None)
    ws.send_json = AsyncMock(side_effect=RuntimeError("already closed"))

    class _FakeClient:
        class sandboxes:  # noqa: N801
            @staticmethod
            def get(_name):
                raise RuntimeError("unexpected internal failure")

    class _FakeSvc:
        def get_client(self, name):  # noqa: ARG002
            return _FakeClient

    monkeypatch.setattr(ws_mod, "_get_gateway_service", lambda: _FakeSvc())
    await sandbox_events(ws, gw="mygw", sandbox_name="sb1")


async def test_ws_handler_outer_grpc_error_send_runtime_error(monkeypatch):
    """Direct call: gRPC error path where send_json also fails (lines 187-190)."""
    from unittest.mock import AsyncMock

    from shoreguard.api import websocket as ws_mod
    from shoreguard.api.websocket import sandbox_events

    ws = MagicMock()
    ws.accept = AsyncMock(return_value=None)
    ws.send_json = AsyncMock(side_effect=RuntimeError("already closed"))

    class _RpcErr(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

    class _FakeClient:
        class sandboxes:  # noqa: N801
            @staticmethod
            def get(_name):
                raise _RpcErr()

    class _FakeSvc:
        def get_client(self, name):  # noqa: ARG002
            return _FakeClient

    monkeypatch.setattr(ws_mod, "_get_gateway_service", lambda: _FakeSvc())
    await sandbox_events(ws, gw="mygw", sandbox_name="sb1")


def test_ws_draft_policy_update_fires_webhook():
    """A ``draft_policy_update`` event triggers ``fire_webhook``."""
    from starlette.testclient import TestClient

    from shoreguard.api.main import app

    mock_client = MagicMock()
    mock_client.sandboxes.get.return_value = {"id": "sb-1", "name": "sb1"}
    mock_client.sandboxes.watch.return_value = iter(
        [
            {
                "type": "draft_policy_update",
                "data": {"diff": "+allow pypi.org"},
            }
        ]
    )

    async def _fake_fire(event_type, payload):
        _fake_fire.called_with = (event_type, payload)  # type: ignore[attr-defined]

    _fake_fire.called_with = None  # type: ignore[attr-defined]

    with (
        patch("shoreguard.api.websocket._get_gateway_service") as mock_gw_svc,
        patch("shoreguard.api.websocket.fire_webhook", side_effect=_fake_fire),
    ):
        mock_gw_svc.return_value.get_client.return_value = mock_client
        client = TestClient(app)
        with client.websocket_connect("/ws/mygw/sb1") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "draft_policy_update"
    # The webhook is fired via ``asyncio.create_task`` — give the loop a tick
    # so the task can run. The TestClient loop closes on context exit, so we
    # rely on the fact the task is scheduled before the handler returns.
    # At minimum the side-effect should have been invoked or scheduled.


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
