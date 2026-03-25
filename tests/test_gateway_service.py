"""Tests for GatewayService — connection, lifecycle, Docker, diagnostics."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import grpc
import pytest

import shoreguard.services.gateway as gw_module
from shoreguard.exceptions import GatewayNotConnectedError
from shoreguard.services.gateway import GatewayService, _derive_status

GW = "test-gw"


@pytest.fixture(autouse=True)
def _reset_gateway_state():
    """Reset module-level gateway state before each test."""
    gw_module._reset_clients()
    yield
    gw_module._reset_clients()


@pytest.fixture
def svc():
    return GatewayService()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Temp openshell config directory with patched config function."""
    d = tmp_path / "openshell"
    d.mkdir()
    monkeypatch.setattr("shoreguard.services.gateway.openshell_config_dir", lambda: d)
    return d


def _make_gw_dir(config_dir, name, metadata=None):
    """Create a gateway directory with optional metadata.json."""
    gw_dir = config_dir / "gateways" / name
    gw_dir.mkdir(parents=True, exist_ok=True)
    if metadata is not None:
        (gw_dir / "metadata.json").write_text(json.dumps(metadata))
    return gw_dir


def _mock_proc(*, returncode=0, stdout="", stderr=""):
    """Return a SimpleNamespace matching subprocess.CompletedProcess."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ─── 1A: _derive_status ──────────────────────────────────────────────────────


def test_derive_status_connected():
    assert _derive_status(True, None) == "connected"
    assert _derive_status(True, "running") == "connected"


def test_derive_status_running():
    assert _derive_status(False, "running") == "running"


def test_derive_status_stopped_exited():
    assert _derive_status(False, "exited") == "stopped"


def test_derive_status_stopped_created():
    assert _derive_status(False, "created") == "stopped"


def test_derive_status_stopped_dead():
    assert _derive_status(False, "dead") == "stopped"


def test_derive_status_offline():
    assert _derive_status(False, None) == "offline"
    assert _derive_status(False, "unknown-state") == "offline"


# ─── 1B: Connection management ───────────────────────────────────────────────


def test_get_client_returns_existing(svc):
    """Existing healthy client is returned directly."""
    mock = MagicMock()
    mock.health.return_value = {"status": "healthy"}
    svc.set_client(mock, name=GW)

    result = svc.get_client(name=GW)

    assert result is mock
    mock.health.assert_called_once()


def test_get_client_reconnects_on_health_fail(svc):
    """Failed health check triggers reconnection."""
    old_client = MagicMock()
    old_client.health.side_effect = grpc.RpcError()
    svc.set_client(old_client, name=GW)

    new_client = MagicMock()
    new_client.health.return_value = {"status": "healthy"}

    with patch.object(svc, "_try_connect", return_value=new_client):
        result = svc.get_client(name=GW)

    assert result is new_client
    old_client.close.assert_called_once()


def test_get_client_no_name_no_active(svc, config_dir):
    """get_client with no name and no active gateway raises."""
    with pytest.raises(GatewayNotConnectedError, match="No gateway specified"):
        svc.get_client(name=None)


def test_get_client_reconnect_close_fails(svc):
    """Even if close() raises, reconnection proceeds."""
    old_client = MagicMock()
    old_client.health.side_effect = grpc.RpcError()
    old_client.close.side_effect = RuntimeError("close failed")
    svc.set_client(old_client, name=GW)

    new_client = MagicMock()
    new_client.health.return_value = {"status": "ok"}

    with patch.object(svc, "_try_connect", return_value=new_client):
        result = svc.get_client(name=GW)

    assert result is new_client


def test_get_client_backoff_escalation(svc):
    """Backoff doubles after each failure, up to max."""
    with patch.object(svc, "_try_connect", return_value=None):
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)

    entry = gw_module._clients[GW]
    assert entry.backoff == gw_module._BACKOFF_MIN

    # Force past the backoff window
    entry.last_attempt = 0.0
    with patch.object(svc, "_try_connect", return_value=None):
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)

    assert entry.backoff == gw_module._BACKOFF_MIN * gw_module._BACKOFF_FACTOR


def test_backoff_prevents_rapid_reconnect(svc):
    """After failed connection, subsequent calls within backoff raise."""
    with patch.object(svc, "_try_connect", return_value=None):
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)

    with patch.object(svc, "_try_connect") as mock_connect:
        with pytest.raises(GatewayNotConnectedError):
            svc.get_client(name=GW)
        mock_connect.assert_not_called()


def test_set_client_none_pops(svc):
    """set_client(None) removes the gateway entry."""
    svc.set_client(MagicMock(), name=GW)
    assert GW in gw_module._clients
    svc.set_client(None, name=GW)
    assert GW not in gw_module._clients


def test_set_client_no_name_no_active_is_noop(svc, config_dir):
    """set_client with no name and no active gateway does nothing."""
    svc.set_client(MagicMock())
    assert len(gw_module._clients) == 0


def test_set_client_creates_entry(svc):
    """set_client for new gateway creates entry."""
    mock = MagicMock()
    svc.set_client(mock, name="new-gw")
    assert gw_module._clients["new-gw"].client is mock
    assert gw_module._clients["new-gw"].backoff == 0.0


def test_reset_backoff_existing(svc):
    """reset_backoff zeroes backoff and last_attempt."""
    entry = gw_module._ClientEntry()
    entry.backoff = 30.0
    entry.last_attempt = 12345.0
    gw_module._clients[GW] = entry

    svc.reset_backoff(name=GW)

    assert entry.backoff == 0.0
    assert entry.last_attempt == 0.0


def test_reset_backoff_nonexistent_is_noop(svc):
    """reset_backoff on unknown gateway does nothing."""
    svc.reset_backoff(name="nonexistent")


def test_try_connect_success(svc, monkeypatch):
    """_try_connect returns client on success."""
    mock = MagicMock()
    mock.health.return_value = {"status": "ok"}
    monkeypatch.setattr(
        "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
        lambda cluster: mock,
    )
    result = svc._try_connect(GW)
    assert result is mock


def test_try_connect_failure(svc, monkeypatch):
    """_try_connect returns None on exception."""
    monkeypatch.setattr(
        "shoreguard.services.gateway.ShoreGuardClient.from_active_cluster",
        MagicMock(side_effect=Exception("connection failed")),
    )
    result = svc._try_connect(GW)
    assert result is None


# ─── 1C: Gateway Discovery & Metadata ────────────────────────────────────────


def test_get_active_name_missing(svc, config_dir):
    """Returns None when active_gateway file does not exist."""
    assert svc.get_active_name() is None


def test_get_active_name_present(svc, config_dir):
    """Returns gateway name read from active_gateway file."""
    (config_dir / "active_gateway").write_text("my-gw\n")
    assert svc.get_active_name() == "my-gw"


def test_get_active_name_empty_file(svc, config_dir):
    """Empty active_gateway file returns None."""
    (config_dir / "active_gateway").write_text("  \n")
    assert svc.get_active_name() is None


def test_read_metadata_not_found(svc, config_dir):
    """Returns error dict when metadata.json does not exist."""
    result = svc.read_metadata("missing-gw")
    assert result["name"] == "missing-gw"
    assert "error" in result


def test_read_metadata_local_type(svc, config_dir):
    """Parses metadata.json and sets type='local' for non-remote gateways."""
    _make_gw_dir(
        config_dir,
        "local-gw",
        {
            "is_remote": False,
            "gateway_endpoint": "localhost:8080",
        },
    )
    result = svc.read_metadata("local-gw")
    assert result["type"] == "local"
    assert result["endpoint"] == "localhost:8080"


def test_read_metadata_remote_type(svc, config_dir):
    """Sets type='remote' when is_remote is true."""
    _make_gw_dir(
        config_dir,
        "remote-gw",
        {
            "is_remote": True,
            "gateway_endpoint": "https://remote:443",
            "remote_host": "user@host",
        },
    )
    result = svc.read_metadata("remote-gw")
    assert result["type"] == "remote"
    assert result["remote_host"] == "user@host"


def test_read_metadata_cloud_type(svc, config_dir):
    """Sets type='cloud' when auth_mode is cloudflare_jwt."""
    _make_gw_dir(
        config_dir,
        "cloud-gw",
        {
            "auth_mode": "cloudflare_jwt",
            "gateway_endpoint": "https://cloud:443",
        },
    )
    result = svc.read_metadata("cloud-gw")
    assert result["type"] == "cloud"
    assert result["auth_mode"] == "cloudflare_jwt"


def test_read_metadata_port(svc, config_dir):
    """Port is included in metadata result."""
    _make_gw_dir(
        config_dir,
        "gw1",
        {
            "gateway_endpoint": "localhost:9090",
            "gateway_port": 9090,
        },
    )
    result = svc.read_metadata("gw1")
    assert result["port"] == 9090


# ─── 1D: list_all and get_info ────────────────────────────────────────────────


def test_list_all_empty_dir(svc, config_dir):
    """Returns empty list when gateways directory doesn't exist."""
    assert svc.list_all() == []


def test_list_all_skips_files(svc, config_dir):
    """Non-directory entries in gateways/ are skipped."""
    gw_dir = config_dir / "gateways"
    gw_dir.mkdir()
    (gw_dir / "not-a-dir.txt").write_text("skip me")
    assert svc.list_all() == []


def test_list_all_with_gateways(svc, config_dir, monkeypatch):
    """Lists gateways with metadata, container status, and active flag."""
    (config_dir / "active_gateway").write_text("gw1")
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    _make_gw_dir(config_dir, "gw2", {"gateway_endpoint": "localhost:8081"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )

    result = svc.list_all()
    assert len(result) == 2
    gw1 = next(g for g in result if g["name"] == "gw1")
    gw2 = next(g for g in result if g["name"] == "gw2")
    assert gw1["active"] is True
    assert gw2["active"] is False
    assert gw1["status"] == "offline"


def test_list_all_connected_gateway(svc, config_dir, monkeypatch):
    """Connected gateway shows version and connected=True."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )

    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok", "version": "1.0.0"}
    svc.set_client(mock_client, name="gw1")

    result = svc.list_all()
    gw = result[0]
    assert gw["connected"] is True
    assert gw["version"] == "1.0.0"
    assert gw["status"] == "connected"


def test_list_all_disconnected_clears_client(svc, config_dir, monkeypatch):
    """Cached client that fails health is cleared in list_all."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )

    mock_client = MagicMock()
    mock_client.health.side_effect = grpc.RpcError()
    svc.set_client(mock_client, name="gw1")

    result = svc.list_all()
    assert result[0]["connected"] is False
    assert "gw1" not in gw_module._clients


def test_get_info_no_active(svc, config_dir):
    """get_info with no active gateway returns configured=False."""
    result = svc.get_info()
    assert result["configured"] is False


def test_get_info_with_name(svc, config_dir, monkeypatch):
    """get_info returns detailed info for a named gateway."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )
    result = svc.get_info(name="gw1")
    assert result["configured"] is True
    assert result["connected"] is False
    assert result["container_status"] == "not_found"


def test_get_info_connected(svc, config_dir, monkeypatch):
    """get_info shows connected with version when client is healthy."""
    (config_dir / "active_gateway").write_text("gw1")
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )

    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok", "version": "2.0"}
    svc.set_client(mock_client, name="gw1")

    result = svc.get_info(name="gw1")
    assert result["connected"] is True
    assert result["version"] == "2.0"
    assert result["active"] is True


def test_health_connected(svc, config_dir):
    """health() returns connected=True when a client responds."""
    (config_dir / "active_gateway").write_text(GW)
    mock = MagicMock()
    mock.health.return_value = {"status": "ok", "version": "1.2.3"}
    svc.set_client(mock, name=GW)

    result = svc.health()
    assert result["connected"] is True
    assert result["version"] == "1.2.3"


def test_health_disconnected(svc, config_dir):
    """health() returns connected=False when no client is set."""
    (config_dir / "active_gateway").write_text(GW)
    result = svc.health()
    assert result["connected"] is False


def test_get_config_delegates_to_client(svc):
    """get_config fetches gateway config via the client."""
    mock = MagicMock()
    mock.health.return_value = {"status": "healthy"}
    mock.get_gateway_config.return_value = {
        "settings": {"log_level": "info"},
        "settings_revision": 3,
    }
    svc.set_client(mock, name=GW)

    with patch.object(svc, "get_active_name", return_value=GW):
        result = svc.get_config()

    mock.get_gateway_config.assert_called_once()
    assert result["settings"]["log_level"] == "info"


# ─── 1E: Docker helpers ──────────────────────────────────────────────────────


def test_get_container_name(svc):
    assert svc._get_container_name("my-gw") == "openshell-cluster-my-gw"


def test_get_container_status_running(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running\n"),
    )
    assert svc._get_container_status("gw1") == "running"


def test_get_container_status_not_found(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1, stderr="No such object"),
    )
    assert svc._get_container_status("gw1") is None


def test_get_container_status_exception(svc, monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.SubprocessError("docker not found")

    monkeypatch.setattr("subprocess.run", _raise)
    assert svc._get_container_status("gw1") is None


def test_docker_start_success(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="openshell-cluster-gw1"),
    )
    result = svc._docker_start_container("gw1")
    assert result["success"] is True


def test_docker_start_failure(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1, stderr="error starting"),
    )
    result = svc._docker_start_container("gw1")
    assert result["success"] is False
    assert "error starting" in result["error"]


def test_docker_start_timeout(svc, monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="docker start", timeout=30)

    monkeypatch.setattr("subprocess.run", _raise)
    result = svc._docker_start_container("gw1")
    assert result["success"] is False
    assert "timed out" in result["error"]


def test_docker_start_generic_exception(svc, monkeypatch):
    def _raise(*a, **kw):
        raise OSError("unexpected")

    monkeypatch.setattr("subprocess.run", _raise)
    result = svc._docker_start_container("gw1")
    assert result["success"] is False


def test_docker_stop_success(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="openshell-cluster-gw1"),
    )
    result = svc._docker_stop_container("gw1")
    assert result["success"] is True


def test_docker_stop_failure(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1, stderr="error stopping"),
    )
    result = svc._docker_stop_container("gw1")
    assert result["success"] is False


def test_docker_stop_timeout(svc, monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="docker stop", timeout=30)

    monkeypatch.setattr("subprocess.run", _raise)
    result = svc._docker_stop_container("gw1")
    assert result["success"] is False
    assert "timed out" in result["error"]


def test_check_docker_daemon_true(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="24.0.0"),
    )
    assert svc._check_docker_daemon() is True


def test_check_docker_daemon_false(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )
    assert svc._check_docker_daemon() is False


def test_check_docker_daemon_exception(svc, monkeypatch):
    def _raise(*a, **kw):
        raise OSError("not found")

    monkeypatch.setattr("subprocess.run", _raise)
    assert svc._check_docker_daemon() is False


# ─── 1F: Port management ─────────────────────────────────────────────────────


def test_get_port_for_gateway_exists(svc, config_dir):
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 8080})
    assert svc._get_port_for_gateway("gw1") == 8080


def test_get_port_for_gateway_missing(svc, config_dir):
    assert svc._get_port_for_gateway("nonexistent") is None


def test_get_port_for_gateway_bad_json(svc, config_dir):
    gw_dir = config_dir / "gateways" / "gw1"
    gw_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text("not json{{{")
    assert svc._get_port_for_gateway("gw1") is None


def test_find_port_blocker_found(svc, config_dir, monkeypatch):
    """Another running gateway on the same port is found."""
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 8080})
    _make_gw_dir(config_dir, "gw2", {"gateway_port": 8080})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )
    assert svc._find_port_blocker("gw1", 8080) == "gw2"


def test_find_port_blocker_not_running(svc, config_dir, monkeypatch):
    """Gateway on same port but not running is not a blocker."""
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 8080})
    _make_gw_dir(config_dir, "gw2", {"gateway_port": 8080})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="exited"),
    )
    assert svc._find_port_blocker("gw1", 8080) is None


def test_find_port_blocker_no_gateways_dir(svc, config_dir):
    """Returns None when gateways dir doesn't exist."""
    assert svc._find_port_blocker("gw1", 8080) is None


def test_get_used_ports(svc, config_dir):
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 8080})
    _make_gw_dir(config_dir, "gw2", {"gateway_port": 9090})
    _make_gw_dir(config_dir, "gw3", {})  # no port
    assert svc._get_used_ports() == {8080, 9090}


def test_get_used_ports_empty(svc, config_dir):
    assert svc._get_used_ports() == set()


def test_next_free_port(svc, config_dir):
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 8080})
    _make_gw_dir(config_dir, "gw2", {"gateway_port": 8081})
    assert svc._next_free_port() == 8082


def test_next_free_port_no_conflict(svc, config_dir):
    assert svc._next_free_port() == 8080


# ─── 1G: Diagnostics ─────────────────────────────────────────────────────────


def test_diagnostics_docker_running(svc, monkeypatch):
    """Docker installed, daemon running, accessible."""
    calls = []

    def _mock_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "docker":
            return _mock_proc(stdout="24.0.0")
        if cmd[0] == "groups":
            return _mock_proc(stdout="user docker wheel")
        if cmd[0] == "openshell":
            return _mock_proc(stdout="openshell 0.1.0")
        return _mock_proc(returncode=1)

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    result = svc.diagnostics()
    assert result["docker_installed"] is True
    assert result["docker_daemon_running"] is True
    assert result["docker_accessible"] is True
    assert result["docker_version"] == "24.0.0"
    assert result["in_docker_group"] is True
    assert result["openshell_installed"] is True
    assert result["openshell_version"] == "openshell 0.1.0"


def test_diagnostics_docker_permission_denied(svc, monkeypatch):
    def _mock_run(cmd, **kw):
        if cmd[0] == "docker":
            return _mock_proc(returncode=1, stderr="Permission denied while connecting")
        return _mock_proc(stdout="user")

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    result = svc.diagnostics()
    assert result["docker_error"] == "Permission denied"
    assert result["docker_daemon_running"] is True


def test_diagnostics_docker_daemon_not_running(svc, monkeypatch):
    def _mock_run(cmd, **kw):
        if cmd[0] == "docker":
            return _mock_proc(returncode=1, stderr="Is the docker daemon running?")
        return _mock_proc(stdout="user")

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    result = svc.diagnostics()
    assert result["docker_error"] == "Docker daemon is not running"


def test_diagnostics_docker_not_responding(svc, monkeypatch):
    def _mock_run(cmd, **kw):
        if cmd[0] == "docker":
            return _mock_proc(returncode=1, stderr="Docker is not responding")
        return _mock_proc(stdout="user")

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    result = svc.diagnostics()
    assert result["docker_error"] == "Docker daemon is not responding"


def test_diagnostics_docker_other_error(svc, monkeypatch):
    def _mock_run(cmd, **kw):
        if cmd[0] == "docker":
            return _mock_proc(returncode=1, stderr="Something else went wrong")
        return _mock_proc(stdout="user")

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    result = svc.diagnostics()
    assert result["docker_error"] == "Something else went wrong"


def test_diagnostics_docker_not_installed(svc, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _mock_proc(stdout="user"))
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    result = svc.diagnostics()
    assert result["docker_installed"] is False
    assert result["openshell_installed"] is False


def test_diagnostics_docker_subprocess_error(svc, monkeypatch):
    call_count = 0

    def _mock_run(cmd, **kw):
        nonlocal call_count
        call_count += 1
        if cmd[0] == "docker":
            raise subprocess.SubprocessError("failed")
        return _mock_proc(stdout="user")

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)

    result = svc.diagnostics()
    assert result["docker_error"] == "failed"


def test_diagnostics_groups_error(svc, monkeypatch):
    def _mock_run(cmd, **kw):
        if cmd[0] == "groups":
            raise OSError("no such command")
        return _mock_proc(returncode=1)

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    result = svc.diagnostics()
    assert result["user_groups"] == []
    assert result["in_docker_group"] is False


# ─── 1H: Lifecycle actions ───────────────────────────────────────────────────


def test_select_not_found(svc, config_dir):
    result = svc.select("nonexistent")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_select_container_not_running(svc, config_dir, monkeypatch):
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="exited"),
    )
    result = svc.select("gw1")
    assert result["success"] is True
    assert result["connected"] is False
    assert "warning" in result
    assert (config_dir / "active_gateway").read_text() == "gw1"


def test_select_connect_ok(svc, config_dir, monkeypatch):
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    (config_dir / "active_gateway").write_text("gw1")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )

    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok"}

    with patch.object(svc, "get_client", return_value=mock_client):
        result = svc.select("gw1")

    assert result["success"] is True
    assert result["connected"] is True


def test_select_tls_error(svc, config_dir, monkeypatch):
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    (config_dir / "active_gateway").write_text("gw1")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )

    with patch.object(svc, "get_client", side_effect=Exception("SSL certificate verify failed")):
        result = svc.select("gw1")

    assert result["success"] is True
    assert result["connected"] is False
    assert "TLS" in result["warning"]


def test_select_generic_error(svc, config_dir, monkeypatch):
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    (config_dir / "active_gateway").write_text("gw1")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )

    with patch.object(svc, "get_client", side_effect=Exception("some other error")):
        result = svc.select("gw1")

    assert result["success"] is True
    assert result["connected"] is False


def test_start_no_active(svc, config_dir):
    result = svc.start()
    assert result["success"] is False
    assert "No active gateway" in result["error"]


def test_start_docker_not_running(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("gw1")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )
    with patch.object(svc, "_check_docker_daemon", return_value=False):
        result = svc.start()
    assert result["success"] is False
    assert "Docker daemon" in result["error"]


def test_start_already_running(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="running"),
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("test")),
    ):
        result = svc.start()

    assert result["success"] is True
    assert "already running" in result["output"]


def test_start_exited_port_blocked(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="exited"),
        patch.object(svc, "_get_port_for_gateway", return_value=8080),
        patch.object(svc, "_find_port_blocker", return_value="other-gw"),
    ):
        result = svc.start()

    assert result["success"] is False
    assert "already in use" in result["error"]


def test_start_exited_success(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("gw1")
    monkeypatch.setattr("time.sleep", lambda _: None)

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="exited"),
        patch.object(svc, "_get_port_for_gateway", return_value=None),
        patch.object(svc, "_docker_start_container", return_value={"success": True}),
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("retry")),
    ):
        result = svc.start()

    assert result["success"] is True


def test_start_no_container_openshell(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value=None),
        patch.object(svc, "_run_openshell", return_value={"success": True, "output": "started"}),
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("retry")),
    ):
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")
        result = svc.start()

    assert result["success"] is True


def test_start_no_container_no_openshell(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value=None),
    ):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        result = svc.start()

    assert result["success"] is False
    assert "openshell CLI not found" in result["error"]


def test_stop_no_active(svc, config_dir):
    result = svc.stop()
    assert result["success"] is False
    assert "No active gateway" in result["error"]


def test_stop_already_stopped(svc, config_dir):
    (config_dir / "active_gateway").write_text("gw1")
    with patch.object(svc, "_get_container_status", return_value="exited"):
        result = svc.stop()
    assert result["success"] is True
    assert "already stopped" in result["output"]


def test_stop_success_clears_client(svc, config_dir):
    (config_dir / "active_gateway").write_text("gw1")
    svc.set_client(MagicMock(), name="gw1")

    with (
        patch.object(svc, "_get_container_status", return_value="running"),
        patch.object(svc, "_docker_stop_container", return_value={"success": True}),
    ):
        result = svc.stop()

    assert result["success"] is True
    assert "gw1" not in gw_module._clients


def test_restart_calls_stop_then_start(svc, config_dir):
    (config_dir / "active_gateway").write_text("gw1")
    calls = []

    def _mock_stop(name=None):
        calls.append("stop")
        return {"success": True}

    def _mock_start(name=None):
        calls.append("start")
        return {"success": True}

    with (
        patch.object(svc, "stop", side_effect=_mock_stop),
        patch.object(svc, "start", side_effect=_mock_start),
    ):
        result = svc.restart()

    assert calls == ["stop", "start"]
    assert result["success"] is True


def test_create_no_openshell(svc, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    result = svc.create("gw1")
    assert result["success"] is False
    assert "openshell CLI not found" in result["error"]


def test_create_docker_not_running(svc, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")
    with patch.object(svc, "_check_docker_daemon", return_value=False):
        result = svc.create("gw1")
    assert result["success"] is False
    assert "Docker daemon" in result["error"]


def test_create_port_conflict(svc, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")
    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_find_port_blocker", return_value="other-gw"),
    ):
        result = svc.create("gw1", port=8080)
    assert result["success"] is False
    assert "already configured" in result["error"]


def test_create_auto_port(svc, config_dir, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_next_free_port", return_value=8085),
        patch.object(svc, "_run_openshell", return_value={"success": True}) as mock_run,
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("test")),
    ):
        result = svc.create("gw1")

    assert result["success"] is True
    args_call = mock_run.call_args
    assert "8085" in args_call[0][0]


def test_create_with_gpu_and_remote(svc, config_dir, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_find_port_blocker", return_value=None),
        patch.object(svc, "_run_openshell", return_value={"success": True}) as mock_run,
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("test")),
    ):
        svc.create("gw1", port=9090, remote_host="user@host", gpu=True)

    args = mock_run.call_args[0][0]
    assert "--remote" in args
    assert "user@host" in args
    assert "--gpu" in args
    assert "9090" in args


def test_destroy_clears_active_client(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("gw1")
    svc.set_client(MagicMock(), name="gw1")
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")

    with patch.object(svc, "_run_openshell", return_value={"success": True}):
        result = svc.destroy("gw1")

    assert result["success"] is True
    assert "gw1" not in gw_module._clients


def test_destroy_no_openshell(svc, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    result = svc.destroy("gw1")
    assert result["success"] is False


def test_destroy_non_active_no_client_clear(svc, config_dir, monkeypatch):
    (config_dir / "active_gateway").write_text("other-gw")
    svc.set_client(MagicMock(), name="gw1")
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")

    with patch.object(svc, "_run_openshell", return_value={"success": True}):
        svc.destroy("gw1")

    # Client for non-active gateway should not be cleared
    assert "gw1" in gw_module._clients


# ─── 1I: CLI Runner & Helpers ────────────────────────────────────────────────


def test_run_openshell_success(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="gateway started"),
    )
    result = svc._run_openshell(["gateway", "start"])
    assert result["success"] is True
    assert result["output"] == "gateway started"


def test_run_openshell_success_stderr_fallback(svc, monkeypatch):
    """When stdout is empty, stderr is used as output."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="", stderr="started via stderr"),
    )
    result = svc._run_openshell(["gateway", "start"])
    assert result["success"] is True
    assert result["output"] == "started via stderr"


def test_run_openshell_failure(svc, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1, stderr="gateway error"),
    )
    result = svc._run_openshell(["gateway", "start"])
    assert result["success"] is False
    assert result["error"] == "gateway error"


def test_run_openshell_failure_exit_code_only(svc, monkeypatch):
    """When both stdout and stderr empty, exit code shown."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=42, stdout="", stderr=""),
    )
    result = svc._run_openshell(["gateway", "start"])
    assert result["success"] is False
    assert "42" in result["error"]


def test_run_openshell_timeout(svc, monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="openshell", timeout=30)

    monkeypatch.setattr("subprocess.run", _raise)
    result = svc._run_openshell(["gateway", "start"], timeout=30)
    assert result["success"] is False
    assert "timed out" in result["error"]


def test_run_openshell_exception(svc, monkeypatch):
    def _raise(*a, **kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("subprocess.run", _raise)
    result = svc._run_openshell(["gateway", "start"])
    assert result["success"] is False


def test_write_active_gateway(svc, config_dir):
    svc._write_active_gateway("my-gw")
    assert (config_dir / "active_gateway").read_text() == "my-gw"


# ─── 2: Additional mutant-killing tests ──────────────────────────────────────


# ── diagnostics mutants ──────────────────────────────────────────────────────


def test_diagnostics_default_false_flags(svc, monkeypatch):
    """Default state: docker_accessible and docker_daemon_running are False."""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _mock_proc(stdout="user"))
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    result = svc.diagnostics()
    assert result["docker_accessible"] is False
    assert result["docker_daemon_running"] is False
    assert result["openshell_version"] is None


def test_diagnostics_user_field(svc, monkeypatch):
    """User field comes from USER env var."""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _mock_proc(stdout="user"))
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setenv("USER", "testuser42")

    result = svc.diagnostics()
    assert result["user"] == "testuser42"


def test_diagnostics_groups_parsing(svc, monkeypatch):
    """Groups are split into a list from stdout."""

    def _mock_run(cmd, **kw):
        if cmd[0] == "groups":
            return _mock_proc(stdout="wheel audio video")
        return _mock_proc(returncode=1)

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    result = svc.diagnostics()
    assert result["user_groups"] == ["wheel", "audio", "video"]
    assert result["in_docker_group"] is False


def test_diagnostics_groups_returncode_nonzero(svc, monkeypatch):
    """When groups command fails, user_groups is empty list."""

    def _mock_run(cmd, **kw):
        if cmd[0] == "groups":
            return _mock_proc(returncode=1, stdout="")
        return _mock_proc(returncode=1)

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    result = svc.diagnostics()
    assert result["user_groups"] == []
    assert result["in_docker_group"] is False


def test_diagnostics_openshell_version_failure(svc, monkeypatch):
    """openshell installed but --version fails: version stays None."""

    def _mock_run(cmd, **kw):
        if cmd[0] == "openshell":
            return _mock_proc(returncode=1)
        if cmd[0] == "groups":
            return _mock_proc(stdout="user")
        return _mock_proc(returncode=1)

    monkeypatch.setattr("subprocess.run", _mock_run)
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/bin/openshell" if cmd == "openshell" else None,
    )

    result = svc.diagnostics()
    assert result["openshell_installed"] is True
    assert result["openshell_version"] is None


# ── start mutants ────────────────────────────────────────────────────────────


def test_start_already_running_exact_message(svc, config_dir):
    """Assert the exact output message when already running."""
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="running"),
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("test")),
    ):
        result = svc.start()

    assert result["output"] == "Gateway is already running"


def test_start_already_running_explicit_name_different_from_active(svc, config_dir):
    """When name is passed and differs from active, get_client is NOT called."""
    (config_dir / "active_gateway").write_text("other-gw")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="running"),
        patch.object(svc, "get_client") as mock_get_client,
    ):
        result = svc.start(name="gw1")

    assert result["success"] is True
    assert result["output"] == "Gateway is already running"
    mock_get_client.assert_not_called()


def test_start_retry_loop_count(svc, config_dir, monkeypatch):
    """The retry loop runs exactly 10 iterations when get_client keeps failing."""
    (config_dir / "active_gateway").write_text("gw1")
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="exited"),
        patch.object(svc, "_get_port_for_gateway", return_value=None),
        patch.object(svc, "_docker_start_container", return_value={"success": True}),
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("retry")),
    ):
        svc.start()

    assert len(sleep_calls) == 10
    assert all(s == 2 for s in sleep_calls)


def test_start_created_status_starts_container(svc, config_dir, monkeypatch):
    """Container in 'created' status gets docker-started."""
    (config_dir / "active_gateway").write_text("gw1")
    monkeypatch.setattr("time.sleep", lambda _: None)

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="created"),
        patch.object(svc, "_get_port_for_gateway", return_value=None),
        patch.object(svc, "_docker_start_container", return_value={"success": True}) as mock_start,
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("retry")),
    ):
        result = svc.start()

    assert result["success"] is True
    mock_start.assert_called_once_with("gw1")


def test_start_dead_status_starts_container(svc, config_dir, monkeypatch):
    """Container in 'dead' status gets docker-started."""
    (config_dir / "active_gateway").write_text("gw1")
    monkeypatch.setattr("time.sleep", lambda _: None)

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="dead"),
        patch.object(svc, "_get_port_for_gateway", return_value=None),
        patch.object(svc, "_docker_start_container", return_value={"success": True}) as mock_start,
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("retry")),
    ):
        result = svc.start()

    assert result["success"] is True
    mock_start.assert_called_once_with("gw1")


def test_start_exited_explicit_name_skips_retry(svc, config_dir, monkeypatch):
    """When name differs from active, retry loop is skipped after docker start."""
    (config_dir / "active_gateway").write_text("other-gw")
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_get_container_status", return_value="exited"),
        patch.object(svc, "_get_port_for_gateway", return_value=None),
        patch.object(svc, "_docker_start_container", return_value={"success": True}),
        patch.object(svc, "get_client") as mock_gc,
    ):
        result = svc.start(name="gw1")

    assert result["success"] is True
    assert len(sleep_calls) == 0
    mock_gc.assert_not_called()


# ── _docker_start_container mutants ──────────────────────────────────────────


def test_docker_start_output_contains_container_name(svc, monkeypatch):
    """Success output includes the container name."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="ok"),
    )
    result = svc._docker_start_container("my-gw")
    assert "openshell-cluster-my-gw" in result["output"]


def test_docker_start_error_exit_code_fallback(svc, monkeypatch):
    """When stderr is empty on failure, error shows exit code."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=5, stderr=""),
    )
    result = svc._docker_start_container("gw1")
    assert result["success"] is False
    assert result["error"] == "Exit code 5"


# ── _docker_stop_container mutants ───────────────────────────────────────────


def test_docker_stop_output_contains_container_name(svc, monkeypatch):
    """Success output includes the container name."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="ok"),
    )
    result = svc._docker_stop_container("my-gw")
    assert "openshell-cluster-my-gw" in result["output"]


def test_docker_stop_error_exit_code_fallback(svc, monkeypatch):
    """When stderr is empty on failure, error shows exit code."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=3, stderr=""),
    )
    result = svc._docker_stop_container("gw1")
    assert result["success"] is False
    assert result["error"] == "Exit code 3"


def test_docker_stop_generic_exception(svc, monkeypatch):
    """Generic exception in stop is caught and reported."""

    def _raise(*a, **kw):
        raise RuntimeError("unexpected stop error")

    monkeypatch.setattr("subprocess.run", _raise)
    result = svc._docker_stop_container("gw1")
    assert result["success"] is False
    assert result["error"] == "unexpected stop error"


# ── select mutants ───────────────────────────────────────────────────────────


def test_select_warning_contains_status(svc, config_dir, monkeypatch):
    """Warning message includes the container status."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="exited"),
    )
    result = svc.select("gw1")
    assert "exited" in result["warning"]


def test_select_container_status_none(svc, config_dir, monkeypatch):
    """When container_status is None, warning says 'not found'."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )
    result = svc.select("gw1")
    assert result["success"] is True
    assert result["connected"] is False
    assert "not found" in result["warning"]


# ── create mutants ───────────────────────────────────────────────────────────


def test_create_port_zero_uses_auto_port(svc, config_dir, monkeypatch):
    """port=0 triggers _next_free_port just like port=None."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")
    (config_dir / "active_gateway").write_text("gw1")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_next_free_port", return_value=9999) as mock_nfp,
        patch.object(svc, "_run_openshell", return_value={"success": True}) as mock_run,
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("test")),
    ):
        result = svc.create("gw1", port=0)

    assert result["success"] is True
    mock_nfp.assert_called_once()
    assert "9999" in mock_run.call_args[0][0]


def test_create_writes_active_gateway_on_success(svc, config_dir, monkeypatch):
    """_write_active_gateway is called on successful create."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_next_free_port", return_value=8080),
        patch.object(svc, "_run_openshell", return_value={"success": True}),
        patch.object(svc, "_write_active_gateway") as mock_wag,
        patch.object(svc, "get_client", side_effect=GatewayNotConnectedError("test")),
    ):
        svc.create("new-gw")

    mock_wag.assert_called_once_with("new-gw")


def test_create_failure_does_not_write_active(svc, config_dir, monkeypatch):
    """_write_active_gateway is NOT called when create fails."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")

    with (
        patch.object(svc, "_check_docker_daemon", return_value=True),
        patch.object(svc, "_next_free_port", return_value=8080),
        patch.object(svc, "_run_openshell", return_value={"success": False, "error": "fail"}),
        patch.object(svc, "_write_active_gateway") as mock_wag,
    ):
        svc.create("new-gw")

    mock_wag.assert_not_called()


# ── destroy mutants ──────────────────────────────────────────────────────────


def test_destroy_passes_correct_args(svc, config_dir, monkeypatch):
    """destroy passes ['gateway', 'destroy', '--name', name] to _run_openshell."""
    (config_dir / "active_gateway").write_text("other-gw")
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/openshell")

    with patch.object(svc, "_run_openshell", return_value={"success": True}) as mock_run:
        svc.destroy("gw1")

    mock_run.assert_called_once_with(
        ["gateway", "destroy", "--name", "gw1"],
        timeout=30,
    )


# ── _get_container_status mutants ────────────────────────────────────────────


def test_get_container_status_correct_docker_command(svc, monkeypatch):
    """Verify the correct docker inspect command is constructed."""
    captured_args = []

    def _capture_run(cmd, **kw):
        captured_args.append(cmd)
        return _mock_proc(stdout="running")

    monkeypatch.setattr("subprocess.run", _capture_run)
    svc._get_container_status("my-gw")

    assert captured_args[0] == [
        "docker",
        "inspect",
        "-f",
        "{{.State.Status}}",
        "openshell-cluster-my-gw",
    ]


# ── _run_openshell mutants ───────────────────────────────────────────────────


def test_run_openshell_error_uses_stdout_when_stderr_empty(svc, monkeypatch):
    """On failure with empty stderr, stdout is used as error message."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1, stdout="stdout error", stderr=""),
    )
    result = svc._run_openshell(["gateway", "start"])
    assert result["success"] is False
    assert result["error"] == "stdout error"


# ── health mutants ───────────────────────────────────────────────────────────


def test_health_gateway_name_field(svc, config_dir):
    """health() result includes the active gateway name."""
    (config_dir / "active_gateway").write_text("my-gw")
    result = svc.health()
    assert result["gateway_name"] == "my-gw"


def test_health_status_field(svc, config_dir):
    """health() result includes health_status from client."""
    (config_dir / "active_gateway").write_text(GW)
    mock = MagicMock()
    mock.health.return_value = {"status": "healthy", "version": "1.0"}
    svc.set_client(mock, name=GW)

    result = svc.health()
    assert result["health_status"] == "healthy"
    assert result["connected"] is True


# ── list_all mutants ─────────────────────────────────────────────────────────


def test_list_all_container_status_field(svc, config_dir, monkeypatch):
    """Each gateway in list_all has a container_status field."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )

    result = svc.list_all()
    assert result[0]["container_status"] == "running"


def test_list_all_no_version_when_not_connected(svc, config_dir, monkeypatch):
    """version key is not set when gateway is not connected."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )

    result = svc.list_all()
    assert "version" not in result[0]


def test_list_all_no_version_when_health_returns_none(svc, config_dir, monkeypatch):
    """version key is not set when health returns no version."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )

    mock_client = MagicMock()
    mock_client.health.return_value = {"status": "ok"}
    svc.set_client(mock_client, name="gw1")

    result = svc.list_all()
    assert "version" not in result[0]


# ── get_info mutants ─────────────────────────────────────────────────────────


def test_get_info_container_not_found(svc, config_dir, monkeypatch):
    """container_status is 'not_found' when no container exists."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(returncode=1),
    )
    result = svc.get_info(name="gw1")
    assert result["container_status"] == "not_found"


def test_get_info_status_field(svc, config_dir, monkeypatch):
    """get_info includes a derived status field."""
    _make_gw_dir(config_dir, "gw1", {"gateway_endpoint": "localhost:8080"})
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )
    result = svc.get_info(name="gw1")
    assert result["status"] == "running"


# ── _find_port_blocker mutants ───────────────────────────────────────────────


def test_find_port_blocker_skips_non_directory(svc, config_dir, monkeypatch):
    """Non-directory entries in gateways dir are skipped."""
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 8080})
    gw_dir = config_dir / "gateways"
    (gw_dir / "not-a-dir.txt").write_text("skip me")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _mock_proc(stdout="running"),
    )
    # Should not crash or return the file as a blocker
    result = svc._find_port_blocker("other-gw", 8080)
    assert result == "gw1"


# ── _get_used_ports mutants ──────────────────────────────────────────────────


def test_get_used_ports_skips_non_directory(svc, config_dir):
    """Non-directory entries in gateways dir are skipped."""
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 8080})
    gw_dir = config_dir / "gateways"
    (gw_dir / "stale-file.txt").write_text("not a gateway")
    assert svc._get_used_ports() == {8080}


# ── _next_free_port mutants ──────────────────────────────────────────────────


def test_next_free_port_custom_start(svc, config_dir):
    """next_free_port with custom start returns that start when no conflicts."""
    assert svc._next_free_port(start=9000) == 9000


def test_next_free_port_custom_start_with_conflict(svc, config_dir):
    """next_free_port skips past conflicting ports from custom start."""
    _make_gw_dir(config_dir, "gw1", {"gateway_port": 9000})
    _make_gw_dir(config_dir, "gw2", {"gateway_port": 9001})
    assert svc._next_free_port(start=9000) == 9002
