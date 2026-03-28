"""Tests for LocalGatewayManager — Docker lifecycle, diagnostics, port management."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import grpc
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.exceptions import GatewayNotConnectedError
from shoreguard.models import Base
from shoreguard.services.gateway import GatewayService
from shoreguard.services.local_gateway import LocalGatewayManager
from shoreguard.services.registry import GatewayRegistry

GW = "test-gw"


@pytest.fixture
def registry():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    reg = GatewayRegistry(factory)
    yield reg
    engine.dispose()


@pytest.fixture
def gw_svc(registry):
    return GatewayService(registry)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    d = tmp_path / "openshell"
    d.mkdir()
    monkeypatch.setattr("shoreguard.services.gateway.openshell_config_dir", lambda: d)
    monkeypatch.setattr("shoreguard.services.local_gateway.openshell_config_dir", lambda: d)
    return d


@pytest.fixture
def mgr(gw_svc):
    return LocalGatewayManager(gw_svc)


# ─── Diagnostics ─────────────────────────────────────────────────────────────


@patch("shoreguard.services.local_gateway.shutil.which")
@patch("shoreguard.services.local_gateway.subprocess.run")
def test_diagnostics_docker_not_installed(mock_run, mock_which, mgr):
    """Diagnostics when docker is not installed."""
    mock_which.return_value = None
    # groups command
    mock_run.return_value = MagicMock(returncode=0, stdout="flo wheel")
    result = mgr.diagnostics()
    assert result["docker_installed"] is False
    assert result["docker_daemon_running"] is False


@patch("shoreguard.services.local_gateway.shutil.which")
@patch("shoreguard.services.local_gateway.subprocess.run")
def test_diagnostics_docker_running(mock_run, mock_which, mgr):
    """Diagnostics when docker is installed and running."""
    mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

    def run_side_effect(args, **kwargs):
        if args[0] == "docker":
            return MagicMock(returncode=0, stdout="24.0.7")
        if args[0] == "groups":
            return MagicMock(returncode=0, stdout="flo docker wheel")
        return MagicMock(returncode=1, stdout="", stderr="")

    mock_run.side_effect = run_side_effect
    result = mgr.diagnostics()
    assert result["docker_installed"] is True
    assert result["docker_daemon_running"] is True
    assert result["docker_accessible"] is True
    assert result["docker_version"] == "24.0.7"
    assert result["in_docker_group"] is True


@patch("shoreguard.services.local_gateway.shutil.which")
@patch("shoreguard.services.local_gateway.subprocess.run")
def test_diagnostics_docker_permission_denied(mock_run, mock_which, mgr):
    """Diagnostics when docker has permission issues."""
    mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

    def run_side_effect(args, **kwargs):
        if args[0] == "docker":
            return MagicMock(returncode=1, stdout="", stderr="permission denied while trying")
        if args[0] == "groups":
            return MagicMock(returncode=0, stdout="flo wheel")
        return MagicMock(returncode=1)

    mock_run.side_effect = run_side_effect
    result = mgr.diagnostics()
    assert result["docker_installed"] is True
    assert result["docker_error"] == "Permission denied"
    assert result["docker_daemon_running"] is True
    assert result["in_docker_group"] is False


# ─── Start / Stop / Restart ──────────────────────────────────────────────────


def test_start_no_active_gateway(mgr, config_dir):
    """Start without any active gateway returns error."""
    result = mgr.start()
    assert result["success"] is False
    assert "No active gateway" in result["error"]


def test_stop_no_active_gateway(mgr, config_dir):
    """Stop without any active gateway returns error."""
    result = mgr.stop()
    assert result["success"] is False
    assert "No active gateway" in result["error"]


def test_restart_no_active_gateway(mgr, config_dir):
    """Restart without any active gateway returns error."""
    result = mgr.restart()
    assert result["success"] is False
    assert "No active gateway" in result["error"]


@patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
def test_start_already_running(mock_status, mock_docker, mgr, config_dir):
    """Start when container is already running."""
    mock_docker.return_value = True
    mock_status.return_value = "running"
    (config_dir / "active_gateway").write_text(GW)
    result = mgr.start()
    assert result["success"] is True
    assert "already running" in result["output"]


@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
def test_stop_already_stopped(mock_status, mgr, config_dir):
    """Stop when container is not running."""
    mock_status.return_value = "exited"
    (config_dir / "active_gateway").write_text(GW)
    result = mgr.stop()
    assert result["success"] is True
    assert "already stopped" in result["output"]


@patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
def test_start_docker_not_running(mock_docker, mgr, config_dir):
    """Start when docker daemon is not running."""
    mock_docker.return_value = False
    (config_dir / "active_gateway").write_text(GW)
    result = mgr.start()
    assert result["success"] is False
    assert "Docker daemon" in result["error"]


@patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
def test_start_exited_container(mock_start, mock_status, mock_docker, mgr, config_dir):
    """Start a previously stopped container."""
    mock_docker.return_value = True
    mock_status.return_value = "exited"
    mock_start.return_value = {"success": True, "output": "Started"}
    (config_dir / "active_gateway").write_text(GW)

    with patch.object(mgr._gw, "get_client", side_effect=GatewayNotConnectedError):
        result = mgr.start()
    assert result["success"] is True


# ─── Create ──────────────────────────────────────────────────────────────────


@patch("shoreguard.services.local_gateway.shutil.which")
def test_create_openshell_not_found(mock_which, mgr):
    """Create fails when openshell CLI is missing."""
    mock_which.return_value = None
    result = mgr.create("new-gw")
    assert result["success"] is False
    assert "openshell CLI not found" in result["error"]


@patch("shoreguard.services.local_gateway.shutil.which")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
def test_create_docker_not_running(mock_docker, mock_which, mgr):
    """Create fails when docker daemon is not running."""
    mock_which.return_value = "/usr/bin/openshell"
    mock_docker.return_value = False
    result = mgr.create("new-gw")
    assert result["success"] is False
    assert "Docker daemon" in result["error"]


# ─── Destroy ─────────────────────────────────────────────────────────────────


@patch("shoreguard.services.local_gateway.shutil.which")
def test_destroy_openshell_not_found(mock_which, mgr):
    """Destroy fails when openshell CLI is missing."""
    mock_which.return_value = None
    result = mgr.destroy("test-gw")
    assert result["success"] is False
    assert "openshell CLI not found" in result["error"]


@patch("shoreguard.services.local_gateway.shutil.which")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
def test_destroy_no_resources(mock_run, mock_which, mgr):
    """Destroy succeeds with no connected client (resources can't be checked)."""
    mock_which.return_value = "/usr/bin/openshell"
    mock_run.return_value = {"success": True, "output": "Destroyed"}
    result = mgr.destroy("test-gw")
    assert result["success"] is True


@patch("shoreguard.services.local_gateway.shutil.which")
def test_destroy_with_resources_no_force(mock_which, mgr):
    """Destroy blocked when gateway has resources and force=False."""
    mock_which.return_value = "/usr/bin/openshell"

    mock_client = MagicMock()
    mock_client.sandboxes.list.return_value = [{"name": "sb1"}]
    mock_client.providers.list.return_value = []

    with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
        result = mgr.destroy("test-gw", force=False)

    assert result["success"] is False
    assert "1 sandbox(es)" in result["error"]
    assert "force=true" in result["error"]


@patch("shoreguard.services.local_gateway.shutil.which")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
def test_destroy_with_resources_force(mock_run, mock_which, mgr):
    """Destroy with force cleans up resources first."""
    mock_which.return_value = "/usr/bin/openshell"
    mock_run.return_value = {"success": True, "output": "Destroyed"}

    mock_client = MagicMock()
    mock_client.sandboxes.list.return_value = [{"name": "sb1"}]
    mock_client.providers.list.return_value = [{"name": "prov1"}]

    with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
        result = mgr.destroy("test-gw", force=True)

    mock_client.sandboxes.delete.assert_called_once_with("sb1")
    mock_client.providers.delete.assert_called_once_with("prov1")
    assert result["success"] is True


@patch("shoreguard.services.local_gateway.shutil.which")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
def test_destroy_sandbox_delete_error_continues(mock_run, mock_which, mgr):
    """Destroy continues cleanup when individual sandbox delete fails."""
    mock_which.return_value = "/usr/bin/openshell"
    mock_run.return_value = {"success": True, "output": "Destroyed"}

    mock_client = MagicMock()
    mock_client.sandboxes.list.return_value = [{"name": "sb1"}, {"name": "sb2"}]
    mock_client.providers.list.return_value = []
    mock_client.sandboxes.delete.side_effect = [
        grpc.RpcError(),
        None,
    ]

    with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
        result = mgr.destroy("test-gw", force=True)

    assert mock_client.sandboxes.delete.call_count == 2
    assert result["success"] is True


@patch("shoreguard.services.local_gateway.shutil.which")
def test_destroy_resource_listing_failure_no_force(mock_which, mgr):
    """Destroy blocked when resource listing fails and force=False."""
    mock_which.return_value = "/usr/bin/openshell"

    mock_client = MagicMock()
    mock_client.sandboxes.list.side_effect = grpc.RpcError()
    mock_client.providers.list.return_value = []

    with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
        result = mgr.destroy("test-gw", force=False)

    assert result["success"] is False
    assert "Could not list resources" in result["error"]


# ─── Port management ─────────────────────────────────────────────────────────


def test_get_port_for_gateway_no_metadata(mgr, config_dir):
    """Returns None when no metadata file exists."""
    port = mgr._get_port_for_gateway("nonexistent")
    assert port is None


def test_get_port_for_gateway_with_metadata(mgr, config_dir):
    """Returns port from metadata.json."""
    gw_dir = config_dir / "gateways" / GW
    gw_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text(json.dumps({"gateway_port": 8080}))
    port = mgr._get_port_for_gateway(GW)
    assert port == 8080


def test_get_port_for_gateway_invalid_json(mgr, config_dir):
    """Returns None for malformed metadata.json."""
    gw_dir = config_dir / "gateways" / GW
    gw_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text("not json")
    port = mgr._get_port_for_gateway(GW)
    assert port is None


def test_next_free_port_empty(mgr, config_dir):
    """Returns start port when no gateways exist."""
    port = mgr._next_free_port()
    assert port == 8080


def test_next_free_port_skips_used(mgr, config_dir):
    """Skips ports already configured for other gateways."""
    gw_dir = config_dir / "gateways" / "gw1"
    gw_dir.mkdir(parents=True)
    (gw_dir / "metadata.json").write_text(json.dumps({"gateway_port": 8080}))
    port = mgr._next_free_port()
    assert port == 8081


def test_next_free_port_overflow_raises(mgr, config_dir):
    """Raises RuntimeError when all ports in range are used."""
    with patch.object(mgr, "_get_used_ports", return_value=set(range(8080, 65536))):
        with pytest.raises(RuntimeError, match="No free ports"):
            mgr._next_free_port()


@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
def test_find_port_blocker_none(mock_status, mgr, config_dir):
    """No blocker when port is free."""
    result = mgr._find_port_blocker(GW, 8080)
    assert result is None


@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
def test_find_port_blocker_found(mock_status, mgr, config_dir):
    """Finds blocker when another running gateway uses the port."""
    mock_status.return_value = "running"
    other_dir = config_dir / "gateways" / "other-gw"
    other_dir.mkdir(parents=True)
    (other_dir / "metadata.json").write_text(json.dumps({"gateway_port": 8080}))
    result = mgr._find_port_blocker(GW, 8080)
    assert result == "other-gw"


# ─── Docker helpers ──────────────────────────────────────────────────────────


def test_get_container_name(mgr):
    """Container name derived from gateway name."""
    assert mgr._get_container_name("my-gw") == "openshell-cluster-my-gw"


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_get_container_status_running(mock_run, mgr):
    """Returns container status from docker inspect."""
    mock_run.return_value = MagicMock(returncode=0, stdout="running\n")
    status = mgr._get_container_status("my-gw")
    assert status == "running"


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_get_container_status_not_found(mock_run, mgr):
    """Returns None when container doesn't exist."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
    status = mgr._get_container_status("my-gw")
    assert status is None


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_docker_start_container_success(mock_run, mgr):
    """Docker start returns success."""
    mock_run.return_value = MagicMock(returncode=0, stdout="started")
    result = mgr._docker_start_container("my-gw")
    assert result["success"] is True


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_docker_start_container_failure(mock_run, mgr):
    """Docker start returns failure with error."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no such container")
    result = mgr._docker_start_container("my-gw")
    assert result["success"] is False
    assert "no such container" in result["error"]


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_docker_start_container_timeout(mock_run, mgr):
    """Docker start times out."""
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker start", timeout=30)
    result = mgr._docker_start_container("my-gw")
    assert result["success"] is False
    assert "timed out" in result["error"]


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_docker_stop_container_success(mock_run, mgr):
    """Docker stop returns success."""
    mock_run.return_value = MagicMock(returncode=0, stdout="stopped")
    result = mgr._docker_stop_container("my-gw")
    assert result["success"] is True


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_docker_stop_container_failure(mock_run, mgr):
    """Docker stop returns failure with error."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no such container")
    result = mgr._docker_stop_container("my-gw")
    assert result["success"] is False
    assert "no such container" in result["error"]


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_docker_stop_container_timeout(mock_run, mgr):
    """Docker stop times out."""
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker stop", timeout=30)
    result = mgr._docker_stop_container("my-gw")
    assert result["success"] is False
    assert "timed out" in result["error"]


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_check_docker_daemon_running(mock_run, mgr):
    """Docker daemon check succeeds."""
    mock_run.return_value = MagicMock(returncode=0, stdout="24.0.7")
    assert mgr._check_docker_daemon() is True


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_check_docker_daemon_not_running(mock_run, mgr):
    """Docker daemon check fails."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="daemon not running")
    assert mgr._check_docker_daemon() is False


# ─── OpenShell CLI ───────────────────────────────────────────────────────────


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_run_openshell_success(mock_run, mgr):
    """openshell command succeeds."""
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    result = mgr._run_openshell(["gateway", "start", "--name", "test"])
    assert result["success"] is True
    assert result["output"] == "ok"


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_run_openshell_failure(mock_run, mgr):
    """openshell command fails."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
    result = mgr._run_openshell(["gateway", "start"])
    assert result["success"] is False
    assert result["error"] == "error msg"


@patch("shoreguard.services.local_gateway.subprocess.run")
def test_run_openshell_timeout(mock_run, mgr):
    """openshell command times out."""
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="openshell", timeout=30)
    result = mgr._run_openshell(["gateway", "start"], timeout=30)
    assert result["success"] is False
    assert "timed out" in result["error"]


# ─── Destroy edge cases ─────────────────────────────────────────────────────


@patch("shoreguard.services.local_gateway.shutil.which", return_value="/usr/bin/openshell")
def test_destroy_list_failure_without_force(mock_which, mgr):
    """Destroy returns error when resource listing fails and force=False."""
    mock_client = MagicMock()
    mock_client.sandboxes.list.side_effect = grpc.RpcError()
    mock_client.providers.list.return_value = []
    with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
        result = mgr.destroy("test-gw", force=False)
    assert result["success"] is False
    assert "Could not list resources" in result["error"]


@patch("shoreguard.services.local_gateway.shutil.which", return_value="/usr/bin/openshell")
@patch("shoreguard.services.local_gateway.subprocess.run")
def test_destroy_list_failure_with_force_proceeds(mock_run, mock_which, mgr):
    """Destroy proceeds when resource listing fails and force=True."""
    mock_client = MagicMock()
    mock_client.sandboxes.list.side_effect = grpc.RpcError()
    mock_client.providers.list.return_value = []
    mock_run.return_value = MagicMock(returncode=0, stdout="destroyed", stderr="")
    with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
        result = mgr.destroy("test-gw", force=True)
    assert result["success"] is True
