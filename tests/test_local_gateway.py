"""Tests for LocalGatewayManager — Docker lifecycle, diagnostics, port management."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, call, patch

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


@patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
def test_start_already_running(mock_status, mock_docker, mgr):
    """Start when container is already running."""
    mock_docker.return_value = True
    mock_status.return_value = "running"
    result = mgr.start(GW)
    assert result["success"] is True
    assert "already running" in result["output"]


@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
def test_stop_already_stopped(mock_status, mgr):
    """Stop when container is not running."""
    mock_status.return_value = "exited"
    result = mgr.stop(GW)
    assert result["success"] is True
    assert "already stopped" in result["output"]


@patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
def test_start_docker_not_running(mock_docker, mgr):
    """Start when docker daemon is not running."""
    mock_docker.return_value = False
    result = mgr.start(GW)
    assert result["success"] is False
    assert "Docker daemon" in result["error"]


@patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
@patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
def test_start_exited_container(mock_start, mock_status, mock_docker, mgr):
    """Start a previously stopped container."""
    mock_docker.return_value = True
    mock_status.return_value = "exited"
    mock_start.return_value = {"success": True, "output": "Started"}

    with patch.object(mgr._gw, "get_client", side_effect=GatewayNotConnectedError):
        result = mgr.start(GW)
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


# ═══════════════════════════════════════════════════════════════════════════════
# NEW TESTS — mutation killers
# ═══════════════════════════════════════════════════════════════════════════════


# ─── Diagnostics: exhaustive field assertions ───────────────────────────────


class TestDiagnosticsExhaustive:
    """Kill mutants in diagnostics() by asserting every field exactly."""

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_not_installed_all_defaults(self, mock_run, mock_which, mgr):
        """When docker is not installed, verify every default field."""
        mock_which.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout="flo wheel")
        result = mgr.diagnostics()
        assert result["docker_installed"] is False
        assert result["docker_daemon_running"] is False
        assert result["docker_accessible"] is False
        assert result["docker_version"] is None
        assert result["docker_error"] is None
        assert result["openshell_installed"] is False
        assert result["openshell_version"] is None
        assert result["user_groups"] == ["flo", "wheel"]
        assert result["in_docker_group"] is False
        assert "user" in result

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_user_from_environ(self, mock_run, mock_which, mgr, monkeypatch):
        """User field comes from os.environ.

        Uses ``monkeypatch.setenv`` instead of ``@patch(..., os.environ, {...})``
        so other environment variables are preserved — notably
        ``MUTANT_UNDER_TEST``, which mutmut injects into its trampolines.
        A full dict replacement would strip it and break mutation testing.
        """
        mock_which.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout="testuser")
        monkeypatch.setenv("USER", "testuser")
        result = mgr.diagnostics()
        assert result["user"] == "testuser"

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_user_defaults_to_unknown(self, mock_run, mock_which, mgr, monkeypatch):
        """User field defaults to 'unknown' when USER env var missing."""
        mock_which.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        monkeypatch.delenv("USER", raising=False)
        result = mgr.diagnostics()
        assert result["user"] == "unknown"

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_running_all_fields(self, mock_run, mock_which, mgr):
        """Docker installed and running — all fields exact."""
        mock_which.side_effect = lambda cmd: (
            "/usr/bin/" + cmd if cmd in ("docker", "openshell") else None
        )

        def run_side_effect(args, **kwargs):
            if args[0] == "docker":
                return MagicMock(returncode=0, stdout="  24.0.7  ")
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo docker")
            if args[0] == "openshell":
                return MagicMock(returncode=0, stdout="  v1.2.3  ")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["docker_installed"] is True
        assert result["docker_daemon_running"] is True
        assert result["docker_accessible"] is True
        assert result["docker_version"] == "24.0.7"
        assert result["docker_error"] is None
        assert result["openshell_installed"] is True
        assert result["openshell_version"] == "v1.2.3"
        assert result["user_groups"] == ["flo", "docker"]
        assert result["in_docker_group"] is True

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_daemon_not_running_error(self, mock_run, mock_which, mgr):
        """Docker installed but daemon not running — specific error message."""
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "docker":
                return MagicMock(returncode=1, stdout="", stderr="Is the docker daemon running?")
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["docker_installed"] is True
        assert result["docker_daemon_running"] is False
        assert result["docker_accessible"] is False
        assert result["docker_error"] == "Docker daemon is not running"

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_not_responding_error(self, mock_run, mock_which, mgr):
        """Docker daemon not responding."""
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "docker":
                return MagicMock(returncode=1, stdout="", stderr="Docker is not responding")
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["docker_error"] == "Docker daemon is not responding"
        assert result["docker_daemon_running"] is False

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_unknown_stderr_error(self, mock_run, mock_which, mgr):
        """Docker fails with unknown stderr — use raw stderr."""
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "docker":
                return MagicMock(returncode=1, stdout="", stderr="some random error")
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["docker_error"] == "some random error"
        assert result["docker_daemon_running"] is False
        assert result["docker_accessible"] is False

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_info_raises_exception(self, mock_run, mock_which, mgr):
        """Docker info subprocess raises OSError."""
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "docker":
                raise OSError("docker binary not executable")
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["docker_installed"] is True
        assert result["docker_error"] == "docker binary not executable"
        assert result["docker_daemon_running"] is False
        assert result["docker_accessible"] is False
        assert result["docker_version"] is None

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_groups_command_fails(self, mock_run, mock_which, mgr):
        """Groups command returns non-zero — empty groups."""
        mock_which.return_value = None

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        result = mgr.diagnostics()
        assert result["user_groups"] == []
        assert result["in_docker_group"] is False

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_groups_command_raises_exception(self, mock_run, mock_which, mgr):
        """Groups command raises SubprocessError — fallback to empty."""
        mock_which.return_value = None
        mock_run.side_effect = subprocess.SubprocessError("groups failed")
        result = mgr.diagnostics()
        assert result["user_groups"] == []
        assert result["in_docker_group"] is False

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_openshell_installed_version_fails(self, mock_run, mock_which, mgr):
        """Openshell installed but version check fails."""
        mock_which.side_effect = lambda cmd: "/usr/bin/openshell" if cmd == "openshell" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo")
            if args[0] == "openshell":
                return MagicMock(returncode=1, stdout="", stderr="error")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["openshell_installed"] is True
        assert result["openshell_version"] is None

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_openshell_version_raises_exception(self, mock_run, mock_which, mgr):
        """Openshell version check raises SubprocessError."""
        mock_which.side_effect = lambda cmd: "/usr/bin/openshell" if cmd == "openshell" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo")
            if args[0] == "openshell":
                raise subprocess.SubprocessError("openshell crash")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["openshell_installed"] is True
        assert result["openshell_version"] is None

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_info_called_with_correct_args(self, mock_run, mock_which, mgr):
        """Verify exact arguments to docker info subprocess call."""
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "docker":
                assert args == ["docker", "info", "--format", "{{.ServerVersion}}"]
                assert kwargs.get("capture_output") is True
                assert kwargs.get("text") is True
                assert kwargs.get("timeout") == 5
                return MagicMock(returncode=0, stdout="24.0.7")
            if args[0] == "groups":
                assert kwargs.get("capture_output") is True
                assert kwargs.get("text") is True
                assert kwargs.get("timeout") == 5
                return MagicMock(returncode=0, stdout="flo")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        mgr.diagnostics()

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_permission_denied_sets_daemon_running_true(self, mock_run, mock_which, mgr):
        """Permission denied means daemon IS running, just not accessible."""
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None

        def run_side_effect(args, **kwargs):
            if args[0] == "docker":
                return MagicMock(returncode=1, stdout="", stderr="Permission denied bla")
            if args[0] == "groups":
                return MagicMock(returncode=0, stdout="flo")
            return MagicMock(returncode=1)

        mock_run.side_effect = run_side_effect
        result = mgr.diagnostics()
        assert result["docker_daemon_running"] is True
        assert result["docker_accessible"] is False
        assert result["docker_error"] == "Permission denied"
        assert result["docker_version"] is None


# ─── Start: exhaustive path tests ──────────────────────────────────────────


class TestStartExhaustive:
    """Kill mutants in start()."""

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    def test_start_docker_not_running_exact_error(self, mock_docker, mgr):
        """Exact error message when docker is down."""
        mock_docker.return_value = False
        result = mgr.start(GW)
        assert result == {
            "success": False,
            "error": "Docker daemon is not running. Start it first: sudo systemctl start docker",
        }

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_start_already_running_exact_output(self, mock_status, mock_docker, mgr):
        """Exact output when already running."""
        mock_docker.return_value = True
        mock_status.return_value = "running"
        result = mgr.start(GW)
        assert result == {"success": True, "output": "Gateway is already running"}

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_start_running_tries_get_client(self, mock_status, mock_docker, mgr):
        """When running, get_client is called to confirm connectivity."""
        mock_docker.return_value = True
        mock_status.return_value = "running"
        with patch.object(mgr._gw, "get_client") as mock_gc:
            mgr.start(GW)
            mock_gc.assert_called_once_with(name=GW)

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_start_running_get_client_error_still_success(self, mock_status, mock_docker, mgr):
        """When running but get_client raises, still returns success."""
        mock_docker.return_value = True
        mock_status.return_value = "running"
        with patch.object(mgr._gw, "get_client", side_effect=GatewayNotConnectedError):
            result = mgr.start(GW)
        assert result["success"] is True

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_port_for_gateway")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    def test_start_exited_port_conflict(
        self, mock_blocker, mock_port, mock_status, mock_docker, mgr
    ):
        """Exited container with port conflict returns detailed error."""
        mock_docker.return_value = True
        mock_status.return_value = "exited"
        mock_port.return_value = 8080
        mock_blocker.return_value = "other-gw"
        result = mgr.start(GW)
        assert result["success"] is False
        assert "Port 8080" in result["error"]
        assert '"other-gw"' in result["error"]
        assert f'"{GW}"' in result["error"]
        assert "Stop it first" in result["error"]

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_port_for_gateway")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
    @patch("shoreguard.services.local_gateway.time.sleep")
    def test_start_exited_no_port_starts_container(
        self, mock_sleep, mock_start, mock_port, mock_status, mock_docker, mgr
    ):
        """Exited container with no port info — starts directly."""
        mock_docker.return_value = True
        mock_status.return_value = "exited"
        mock_port.return_value = None
        mock_start.return_value = {"success": True, "output": "Started"}
        with patch.object(mgr._gw, "get_client") as mock_gc:
            mock_gc.return_value = MagicMock()
            result = mgr.start(GW)
        assert result["success"] is True
        mock_start.assert_called_once_with(GW)

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_port_for_gateway")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
    @patch("shoreguard.services.local_gateway.time.sleep")
    def test_start_exited_no_blocker_starts_container(
        self, mock_sleep, mock_start, mock_blocker, mock_port, mock_status, mock_docker, mgr
    ):
        """Exited container with port but no blocker — starts container."""
        mock_docker.return_value = True
        mock_status.return_value = "exited"
        mock_port.return_value = 8080
        mock_blocker.return_value = None
        mock_start.return_value = {"success": True, "output": "Started"}
        with patch.object(mgr._gw, "get_client") as mock_gc:
            mock_gc.return_value = MagicMock()
            result = mgr.start(GW)
        assert result["success"] is True

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
    @patch("shoreguard.services.local_gateway.time.sleep")
    def test_start_exited_success_resets_backoff(
        self, mock_sleep, mock_start, mock_status, mock_docker, mgr
    ):
        """On successful container start, reset_backoff is called."""
        mock_docker.return_value = True
        mock_status.return_value = "exited"
        mock_start.return_value = {"success": True, "output": "Started"}
        with (
            patch.object(mgr._gw, "reset_backoff") as mock_rb,
            patch.object(mgr._gw, "get_client") as mock_gc,
        ):
            mock_gc.return_value = MagicMock()
            mgr.start(GW)
            mock_rb.assert_called_once_with(name=GW)

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
    @patch("shoreguard.services.local_gateway.time.sleep")
    def test_start_exited_failure_no_backoff_reset(
        self, mock_sleep, mock_start, mock_status, mock_docker, mgr
    ):
        """On failed container start, reset_backoff is NOT called."""
        mock_docker.return_value = True
        mock_status.return_value = "exited"
        mock_start.return_value = {"success": False, "error": "fail"}
        with patch.object(mgr._gw, "reset_backoff") as mock_rb:
            result = mgr.start(GW)
            mock_rb.assert_not_called()
        assert result["success"] is False

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
    @patch("shoreguard.services.local_gateway.time.sleep")
    def test_start_exited_retries_connection(
        self, mock_sleep, mock_start, mock_status, mock_docker, mgr
    ):
        """After start, retries get_client up to startup_retries times."""
        mock_docker.return_value = True
        mock_status.return_value = "created"
        mock_start.return_value = {"success": True, "output": "Started"}

        with patch.object(mgr._gw, "get_client", side_effect=GatewayNotConnectedError) as mock_gc:
            with patch.object(mgr._gw, "reset_backoff"):
                mgr.start(GW)
            # Default startup_retries=10, so 10 calls
            assert mock_gc.call_count == 10
        assert mock_sleep.call_count == 10

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_start_container")
    @patch("shoreguard.services.local_gateway.time.sleep")
    def test_start_exited_breaks_on_first_success(
        self, mock_sleep, mock_start, mock_status, mock_docker, mgr
    ):
        """get_client loop breaks on first successful connection."""
        mock_docker.return_value = True
        mock_status.return_value = "dead"
        mock_start.return_value = {"success": True, "output": "Started"}
        call_count = 0

        def gc_effect(name):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise GatewayNotConnectedError
            return MagicMock()

        with patch.object(mgr._gw, "get_client", side_effect=gc_effect):
            with patch.object(mgr._gw, "reset_backoff"):
                mgr.start(GW)
        assert call_count == 3
        assert mock_sleep.call_count == 3

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_start_no_container_openshell_not_found(
        self, mock_which, mock_status, mock_docker, mgr
    ):
        """No container, openshell not installed — returns error."""
        mock_docker.return_value = True
        mock_status.return_value = None
        mock_which.return_value = None
        result = mgr.start(GW)
        assert result == {"success": False, "error": "openshell CLI not found"}

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_start_no_container_runs_openshell(
        self, mock_run, mock_which, mock_status, mock_docker, mgr
    ):
        """No container — falls through to openshell start."""
        mock_docker.return_value = True
        mock_status.return_value = None
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "started"}
        with patch.object(mgr._gw, "get_client") as mock_gc:
            mock_gc.return_value = MagicMock()
            result = mgr.start(GW)
        assert result["success"] is True
        mock_run.assert_called_once_with(["gateway", "start", "--name", GW], timeout=600)

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_start_openshell_success_tries_get_client(
        self, mock_run, mock_which, mock_status, mock_docker, mgr
    ):
        """After openshell start success, get_client is called."""
        mock_docker.return_value = True
        mock_status.return_value = None
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "started"}
        with patch.object(mgr._gw, "get_client") as mock_gc:
            mock_gc.return_value = MagicMock()
            mgr.start(GW)
            mock_gc.assert_called_once_with(name=GW)

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_start_openshell_failure_skips_get_client(
        self, mock_run, mock_which, mock_status, mock_docker, mgr
    ):
        """After openshell start failure, get_client is not called."""
        mock_docker.return_value = True
        mock_status.return_value = None
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": False, "error": "fail"}
        with patch.object(mgr._gw, "get_client") as mock_gc:
            result = mgr.start(GW)
            mock_gc.assert_not_called()
        assert result["success"] is False

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_start_status_created_treated_like_exited(self, mock_status, mock_docker, mgr):
        """Status 'created' goes through the exited/created/dead branch."""
        mock_docker.return_value = True
        mock_status.return_value = "created"
        with (
            patch.object(mgr, "_get_port_for_gateway", return_value=None),
            patch.object(
                mgr, "_docker_start_container", return_value={"success": False, "error": "fail"}
            ) as mock_start,
        ):
            mgr.start(GW)
            mock_start.assert_called_once_with(GW)

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_start_status_dead_treated_like_exited(self, mock_status, mock_docker, mgr):
        """Status 'dead' goes through the exited/created/dead branch."""
        mock_docker.return_value = True
        mock_status.return_value = "dead"
        with (
            patch.object(mgr, "_get_port_for_gateway", return_value=None),
            patch.object(
                mgr, "_docker_start_container", return_value={"success": False, "error": "fail"}
            ) as mock_start,
        ):
            mgr.start(GW)
            mock_start.assert_called_once_with(GW)


# ─── Stop: exhaustive path tests ───────────────────────────────────────────


class TestStopExhaustive:
    """Kill mutants in stop()."""

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_stop_already_stopped_exact(self, mock_status, mgr):
        """Exact return value when already stopped."""
        mock_status.return_value = "exited"
        result = mgr.stop(GW)
        assert result == {"success": True, "output": "Gateway is already stopped"}

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_stop_status_none_treated_as_stopped(self, mock_status, mgr):
        """None status means not running — treat as stopped."""
        mock_status.return_value = None
        result = mgr.stop(GW)
        assert result == {"success": True, "output": "Gateway is already stopped"}

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_stop_container")
    def test_stop_running_calls_docker_stop(self, mock_stop, mock_status, mgr):
        """Running container — calls docker stop."""
        mock_status.return_value = "running"
        mock_stop.return_value = {"success": True, "output": "Stopped"}
        with patch.object(mgr._gw, "set_client") as mock_sc:
            result = mgr.stop(GW)
            mock_sc.assert_called_once_with(None, name=GW)
        assert result["success"] is True

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._docker_stop_container")
    def test_stop_failure_does_not_clear_client(self, mock_stop, mock_status, mgr):
        """Failed stop does NOT clear the client."""
        mock_status.return_value = "running"
        mock_stop.return_value = {"success": False, "error": "failed"}
        with patch.object(mgr._gw, "set_client") as mock_sc:
            result = mgr.stop(GW)
            mock_sc.assert_not_called()
        assert result["success"] is False


# ─── Restart ────────────────────────────────────────────────────────────────


class TestRestart:
    def test_restart_calls_stop_then_start(self, mgr):
        """Restart calls stop then start in order."""
        with (
            patch.object(mgr, "stop") as mock_stop,
            patch.object(mgr, "start", return_value={"success": True}) as mock_start,
        ):
            mock_stop.return_value = {"success": True}
            result = mgr.restart(GW)
            mock_stop.assert_called_once_with(name=GW)
            mock_start.assert_called_once_with(name=GW)
        assert result == {"success": True}


# ─── Create: exhaustive path tests ─────────────────────────────────────────


class TestCreateExhaustive:
    """Kill mutants in create()."""

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_create_no_openshell_exact(self, mock_which, mgr):
        """Exact error when openshell not found."""
        mock_which.return_value = None
        result = mgr.create("gw1")
        assert result == {"success": False, "error": "openshell CLI not found"}

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    def test_create_no_docker_exact(self, mock_docker, mock_which, mgr):
        """Exact error when docker daemon down."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = False
        result = mgr.create("gw1")
        assert result == {
            "success": False,
            "error": "Docker daemon is not running. Start it first: sudo systemctl start docker",
        }

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    def test_create_port_conflict_exact(self, mock_blocker, mock_docker, mock_which, mgr):
        """Port conflict returns exact error with gateway name."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = "other-gw"
        result = mgr.create("gw1", port=8080)
        assert result["success"] is False
        assert "Port 8080" in result["error"]
        assert '"other-gw"' in result["error"]
        assert "Choose a different port" in result["error"]

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._next_free_port")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_no_port_uses_next_free(self, mock_run, mock_nfp, mock_docker, mock_which, mgr):
        """When port=None, auto-selects via _next_free_port."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_nfp.return_value = 8085
        mock_run.return_value = {"success": False, "error": "fail"}
        mgr.create("gw1")
        mock_nfp.assert_called_once()
        mock_run.assert_called_once_with(
            ["gateway", "start", "--name", "gw1", "--port", "8085"],
            timeout=600,
        )

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._next_free_port")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_port_zero_uses_next_free(
        self, mock_run, mock_nfp, mock_docker, mock_which, mgr
    ):
        """port=0 treated as no port, auto-selects."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_nfp.return_value = 8080
        mock_run.return_value = {"success": False, "error": "fail"}
        mgr.create("gw1", port=0)
        mock_nfp.assert_called_once()

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_with_port_no_blocker(
        self, mock_run, mock_blocker, mock_docker, mock_which, mgr
    ):
        """Specified port with no blocker — runs openshell with exact args."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = None
        mock_run.return_value = {"success": False, "error": "fail"}
        mgr.create("gw1", port=9090)
        mock_run.assert_called_once_with(
            ["gateway", "start", "--name", "gw1", "--port", "9090"],
            timeout=600,
        )

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_with_remote_host(self, mock_run, mock_blocker, mock_docker, mock_which, mgr):
        """Remote host adds --remote arg."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = None
        mock_run.return_value = {"success": False, "error": "fail"}
        mgr.create("gw1", port=8080, remote_host="server.example.com")
        mock_run.assert_called_once_with(
            [
                "gateway",
                "start",
                "--name",
                "gw1",
                "--port",
                "8080",
                "--remote",
                "server.example.com",
            ],
            timeout=600,
        )

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_with_gpu(self, mock_run, mock_blocker, mock_docker, mock_which, mgr):
        """GPU flag adds --gpu arg."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = None
        mock_run.return_value = {"success": False, "error": "fail"}
        mgr.create("gw1", port=8080, gpu=True)
        mock_run.assert_called_once_with(
            ["gateway", "start", "--name", "gw1", "--port", "8080", "--gpu"],
            timeout=600,
        )

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_with_remote_and_gpu(self, mock_run, mock_blocker, mock_docker, mock_which, mgr):
        """Both remote_host and gpu flags."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = None
        mock_run.return_value = {"success": False, "error": "fail"}
        mgr.create("gw1", port=8080, remote_host="host", gpu=True)
        mock_run.assert_called_once_with(
            ["gateway", "start", "--name", "gw1", "--port", "8080", "--remote", "host", "--gpu"],
            timeout=600,
        )

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_success_returns_info_with_gpu(
        self, mock_run, mock_blocker, mock_docker, mock_which, mgr
    ):
        """Successful create returns gateway info with gpu field."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = None
        mock_run.return_value = {"success": True, "output": "ok"}
        info = {"name": "gw1", "port": 8080, "status": "running"}
        with (
            patch.object(mgr._gw, "get_client", side_effect=GatewayNotConnectedError),
            patch.object(mgr._gw, "get_info", return_value=info) as mock_info,
        ):
            result = mgr.create("gw1", port=8080, gpu=True)
        mock_info.assert_called_once_with("gw1")
        assert result["gpu"] is True
        assert result["name"] == "gw1"
        assert result["port"] == 8080

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_success_gpu_false(self, mock_run, mock_blocker, mock_docker, mock_which, mgr):
        """Successful create with gpu=False."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = None
        mock_run.return_value = {"success": True, "output": "ok"}
        info = {"name": "gw1"}
        with (
            patch.object(mgr._gw, "get_client", side_effect=GatewayNotConnectedError),
            patch.object(mgr._gw, "get_info", return_value=info),
        ):
            result = mgr.create("gw1", port=8080, gpu=False)
        assert result["gpu"] is False

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_failure_returns_run_result(
        self, mock_run, mock_blocker, mock_docker, mock_which, mgr
    ):
        """Failed openshell — returns raw result, no get_info call."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_blocker.return_value = None
        mock_run.return_value = {"success": False, "error": "openshell failed"}
        with patch.object(mgr._gw, "get_info") as mock_info:
            result = mgr.create("gw1", port=8080)
            mock_info.assert_not_called()
        assert result == {"success": False, "error": "openshell failed"}

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._check_docker_daemon")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._find_port_blocker")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_create_negative_port_uses_next_free(
        self, mock_run, mock_blocker, mock_docker, mock_which, mgr
    ):
        """Negative port treated as no port."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_docker.return_value = True
        mock_run.return_value = {"success": False, "error": "fail"}
        with patch.object(mgr, "_next_free_port", return_value=8080) as mock_nfp:
            mgr.create("gw1", port=-1)
            mock_nfp.assert_called_once()


# ─── Destroy: exhaustive path tests ────────────────────────────────────────


class TestDestroyExhaustive:
    """Kill mutants in destroy()."""

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_no_openshell_exact(self, mock_which, mgr):
        """Exact return value when openshell missing."""
        mock_which.return_value = None
        result = mgr.destroy("gw1")
        assert result == {"success": False, "error": "openshell CLI not found"}

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_destroy_no_client_runs_openshell(self, mock_run, mock_which, mgr):
        """No connected client — still runs destroy command."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "destroyed"}
        with patch.object(mgr._gw, "set_client") as mock_sc:
            result = mgr.destroy("gw1")
            mock_sc.assert_called_once_with(None, name="gw1")
        mock_run.assert_called_once_with(
            ["gateway", "destroy", "--name", "gw1"],
            timeout=30,
        )
        assert result["success"] is True

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_resources_no_force_sandboxes_only(self, mock_which, mgr):
        """Has sandboxes only — error includes sandbox count."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = [{"name": "sb1"}, {"name": "sb2"}]
        mock_client.providers.list.return_value = []
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            result = mgr.destroy("gw1", force=False)
        assert result["success"] is False
        assert "2 sandbox(es)" in result["error"]
        assert "force=true" in result["error"]
        assert result["sandboxes"] == ["sb1", "sb2"]
        assert result["providers"] == []

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_resources_no_force_providers_only(self, mock_which, mgr):
        """Has providers only — error includes provider count."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = []
        mock_client.providers.list.return_value = [{"name": "p1"}]
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            result = mgr.destroy("gw1", force=False)
        assert result["success"] is False
        assert "1 provider(s)" in result["error"]
        assert "sandboxes" not in result["error"]
        assert result["providers"] == ["p1"]
        assert result["sandboxes"] == []

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_resources_no_force_both(self, mock_which, mgr):
        """Has both — error includes 'and'."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = [{"name": "sb1"}]
        mock_client.providers.list.return_value = [{"name": "p1"}, {"name": "p2"}]
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            result = mgr.destroy("gw1", force=False)
        assert "1 sandbox(es) and 2 provider(s)" in result["error"]
        assert result["sandboxes"] == ["sb1"]
        assert result["providers"] == ["p1", "p2"]

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_resource_with_id_fallback(self, mock_which, mgr):
        """Resources with 'id' but no 'name' use id for listing."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = [{"id": "sb-123"}]
        mock_client.providers.list.return_value = [{"id": "p-456"}]
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            result = mgr.destroy("gw1", force=False)
        assert result["sandboxes"] == ["sb-123"]
        assert result["providers"] == ["p-456"]

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_resource_no_name_no_id(self, mock_which, mgr):
        """Resources without name or id produce empty string."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = [{"other": "value"}]
        mock_client.providers.list.return_value = []
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            result = mgr.destroy("gw1", force=False)
        assert result["sandboxes"] == [""]

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_destroy_force_deletes_sandboxes_and_providers(self, mock_run, mock_which, mgr):
        """Force destroy deletes each sandbox and provider by name."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "ok"}
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = [{"name": "sb1"}, {"name": "sb2"}]
        mock_client.providers.list.return_value = [{"name": "p1"}]
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            mgr.destroy("gw1", force=True)
        assert mock_client.sandboxes.delete.call_args_list == [call("sb1"), call("sb2")]
        assert mock_client.providers.delete.call_args_list == [call("p1")]

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_destroy_force_skips_empty_name_sandboxes(self, mock_run, mock_which, mgr):
        """Force destroy skips sandboxes with empty name."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "ok"}
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = [{"name": ""}, {"name": "sb1"}]
        mock_client.providers.list.return_value = [{"name": ""}]
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            mgr.destroy("gw1", force=True)
        mock_client.sandboxes.delete.assert_called_once_with("sb1")
        mock_client.providers.delete.assert_not_called()

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_destroy_force_provider_delete_error_continues(self, mock_run, mock_which, mgr):
        """Provider delete error doesn't stop destruction."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "ok"}
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = []
        mock_client.providers.list.return_value = [{"name": "p1"}, {"name": "p2"}]
        mock_client.providers.delete.side_effect = [OSError("fail"), None]
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            result = mgr.destroy("gw1", force=True)
        assert mock_client.providers.delete.call_count == 2
        assert result["success"] is True

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_sandboxes_none_no_force(self, mock_which, mgr):
        """Sandbox listing returns None (failure) — blocks without force."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_client = MagicMock()
        mock_client.sandboxes.list.side_effect = grpc.RpcError()
        mock_client.providers.list.return_value = []
        with (
            patch.object(mgr, "_get_client_if_connected", return_value=mock_client),
            patch.object(mgr, "_list_resources_safe", side_effect=[None, []]),
        ):
            result = mgr.destroy("gw1", force=False)
        assert result["success"] is False
        assert "Could not list resources" in result["error"]
        assert "force=true" in result["error"]

    @patch("shoreguard.services.local_gateway.shutil.which")
    def test_destroy_providers_none_no_force(self, mock_which, mgr):
        """Provider listing returns None — blocks without force."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_client = MagicMock()
        with (
            patch.object(mgr, "_get_client_if_connected", return_value=mock_client),
            patch.object(mgr, "_list_resources_safe", side_effect=[[], None]),
        ):
            result = mgr.destroy("gw1", force=False)
        assert result["success"] is False
        assert "Could not list resources" in result["error"]

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_destroy_both_none_with_force_proceeds(self, mock_run, mock_which, mgr):
        """Both listings None with force — no error, proceeds to destroy."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "ok"}
        mock_client = MagicMock()
        with (
            patch.object(mgr, "_get_client_if_connected", return_value=mock_client),
            patch.object(mgr, "_list_resources_safe", return_value=None),
        ):
            result = mgr.destroy("gw1", force=True)
        assert result["success"] is True

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_destroy_empty_resources_no_force_proceeds(self, mock_run, mock_which, mgr):
        """Empty resource lists without force — proceeds to destroy."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "ok"}
        mock_client = MagicMock()
        mock_client.sandboxes.list.return_value = []
        mock_client.providers.list.return_value = []
        with patch.object(mgr, "_get_client_if_connected", return_value=mock_client):
            result = mgr.destroy("gw1", force=False)
        assert result["success"] is True

    @patch("shoreguard.services.local_gateway.shutil.which")
    @patch("shoreguard.services.local_gateway.LocalGatewayManager._run_openshell")
    def test_destroy_clears_client_before_openshell(self, mock_run, mock_which, mgr):
        """set_client(None) is called regardless of resource state."""
        mock_which.return_value = "/usr/bin/openshell"
        mock_run.return_value = {"success": True, "output": "ok"}
        with patch.object(mgr._gw, "set_client") as mock_sc:
            mgr.destroy("gw1")
            mock_sc.assert_called_once_with(None, name="gw1")


# ─── Docker helpers: exact assertions ───────────────────────────────────────


class TestDockerHelpersExact:
    """Kill mutants in docker start/stop/status/daemon helpers."""

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_start_exact_args(self, mock_run, mgr):
        """Verify exact args passed to docker start."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        mgr._docker_start_container("my-gw")
        mock_run.assert_called_once_with(
            ["docker", "start", "openshell-cluster-my-gw"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_start_success_exact_output(self, mock_run, mgr):
        """Exact output on success."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        result = mgr._docker_start_container("my-gw")
        assert result == {"success": True, "output": "Container openshell-cluster-my-gw started"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_start_failure_empty_stderr_uses_exit_code(self, mock_run, mgr):
        """Empty stderr uses 'Exit code N' as error."""
        mock_run.return_value = MagicMock(returncode=42, stdout="", stderr="")
        result = mgr._docker_start_container("my-gw")
        assert result == {"success": False, "error": "Exit code 42"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_start_timeout_exact(self, mock_run, mgr):
        """Timeout returns exact message."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=30)
        result = mgr._docker_start_container("my-gw")
        assert result == {"success": False, "error": "Docker start timed out (30s)"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_start_oserror(self, mock_run, mgr):
        """OSError returns str(e)."""
        mock_run.side_effect = OSError("file not found")
        result = mgr._docker_start_container("my-gw")
        assert result == {"success": False, "error": "file not found"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_stop_exact_args(self, mock_run, mgr):
        """Verify exact args passed to docker stop."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        mgr._docker_stop_container("my-gw")
        mock_run.assert_called_once_with(
            ["docker", "stop", "openshell-cluster-my-gw"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_stop_success_exact_output(self, mock_run, mgr):
        """Exact output on success."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        result = mgr._docker_stop_container("my-gw")
        assert result == {"success": True, "output": "Container openshell-cluster-my-gw stopped"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_stop_failure_empty_stderr(self, mock_run, mgr):
        """Empty stderr uses exit code."""
        mock_run.return_value = MagicMock(returncode=7, stdout="", stderr="")
        result = mgr._docker_stop_container("my-gw")
        assert result == {"success": False, "error": "Exit code 7"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_stop_timeout_exact(self, mock_run, mgr):
        """Timeout returns exact message."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=30)
        result = mgr._docker_stop_container("my-gw")
        assert result == {"success": False, "error": "Docker stop timed out (30s)"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_docker_stop_oserror(self, mock_run, mgr):
        """OSError returns str(e)."""
        mock_run.side_effect = OSError("docker crash")
        result = mgr._docker_stop_container("my-gw")
        assert result == {"success": False, "error": "docker crash"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_get_container_status_exact_args(self, mock_run, mgr):
        """Verify exact args to docker inspect."""
        mock_run.return_value = MagicMock(returncode=0, stdout="running\n")
        mgr._get_container_status("my-gw")
        mock_run.assert_called_once_with(
            ["docker", "inspect", "-f", "{{.State.Status}}", "openshell-cluster-my-gw"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_get_container_status_strips_output(self, mock_run, mgr):
        """Output is stripped."""
        mock_run.return_value = MagicMock(returncode=0, stdout="  exited  \n")
        assert mgr._get_container_status("my-gw") == "exited"

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_get_container_status_exception_returns_none(self, mock_run, mgr):
        """OSError returns None."""
        mock_run.side_effect = OSError("no docker")
        assert mgr._get_container_status("my-gw") is None

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_get_container_status_subprocess_error(self, mock_run, mgr):
        """SubprocessError returns None."""
        mock_run.side_effect = subprocess.SubprocessError("fail")
        assert mgr._get_container_status("my-gw") is None

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_check_docker_daemon_exact_args(self, mock_run, mgr):
        """Verify exact args to docker info."""
        mock_run.return_value = MagicMock(returncode=0)
        mgr._check_docker_daemon()
        mock_run.assert_called_once_with(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_check_docker_daemon_nonzero_returns_false(self, mock_run, mgr):
        """Non-zero return code means daemon not running."""
        mock_run.return_value = MagicMock(returncode=1)
        assert mgr._check_docker_daemon() is False

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_check_docker_daemon_subprocess_error(self, mock_run, mgr):
        """SubprocessError returns False."""
        mock_run.side_effect = subprocess.SubprocessError("fail")
        assert mgr._check_docker_daemon() is False

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_check_docker_daemon_oserror(self, mock_run, mgr):
        """OSError returns False."""
        mock_run.side_effect = OSError("no docker")
        assert mgr._check_docker_daemon() is False


# ─── OpenShell CLI: exact assertions ────────────────────────────────────────


class TestRunOpenshellExact:
    """Kill mutants in _run_openshell."""

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_exact_args(self, mock_run, mgr):
        """Verify exact subprocess call."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        mgr._run_openshell(["gateway", "start", "--name", "gw1"], timeout=60)
        mock_run.assert_called_once_with(
            ["openshell", "gateway", "start", "--name", "gw1"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_success_stdout_preferred(self, mock_run, mgr):
        """When both stdout and stderr, stdout is returned."""
        mock_run.return_value = MagicMock(returncode=0, stdout="output here", stderr="warning")
        result = mgr._run_openshell(["test"])
        assert result == {"success": True, "output": "output here"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_success_stderr_fallback(self, mock_run, mgr):
        """When stdout empty but stderr has content, stderr returned as output."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="some info")
        result = mgr._run_openshell(["test"])
        assert result == {"success": True, "output": "some info"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_failure_stderr_preferred(self, mock_run, mgr):
        """On failure, stderr is preferred for error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="output", stderr="error msg")
        result = mgr._run_openshell(["test"])
        assert result == {"success": False, "error": "error msg"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_failure_stdout_fallback(self, mock_run, mgr):
        """On failure, when stderr empty, stdout is used."""
        mock_run.return_value = MagicMock(returncode=1, stdout="output info", stderr="")
        result = mgr._run_openshell(["test"])
        assert result == {"success": False, "error": "output info"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_failure_exit_code_fallback(self, mock_run, mgr):
        """On failure, when both empty, exit code is used."""
        mock_run.return_value = MagicMock(returncode=99, stdout="", stderr="")
        result = mgr._run_openshell(["test"])
        assert result == {"success": False, "error": "Exit code 99"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_timeout_with_value(self, mock_run, mgr):
        """Timeout includes the timeout value."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="openshell", timeout=45)
        result = mgr._run_openshell(["test"], timeout=45)
        assert result == {"success": False, "error": "Command timed out (45s)"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_oserror(self, mock_run, mgr):
        """OSError returns str(e)."""
        mock_run.side_effect = OSError("binary missing")
        result = mgr._run_openshell(["test"])
        assert result == {"success": False, "error": "binary missing"}

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_default_timeout(self, mock_run, mgr):
        """Default timeout is 30."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        mgr._run_openshell(["test"])
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 30

    @patch("shoreguard.services.local_gateway.subprocess.run")
    def test_run_openshell_success_strips_output(self, mock_run, mgr):
        """Output is stripped."""
        mock_run.return_value = MagicMock(returncode=0, stdout="  ok  \n", stderr="")
        result = mgr._run_openshell(["test"])
        assert result["output"] == "ok"


# ─── Internal helpers ───────────────────────────────────────────────────────


class TestGetClientIfConnected:
    """Kill mutants in _get_client_if_connected."""

    def test_returns_cached_client(self, mgr):
        """Returns cached client when available."""
        mock_client = MagicMock()
        with patch.object(mgr._gw, "get_cached_client", return_value=mock_client):
            result = mgr._get_client_if_connected("gw1")
        assert result is mock_client

    def test_cached_none_tries_get_client(self, mgr):
        """When cache miss, tries get_client."""
        mock_client = MagicMock()
        with (
            patch.object(mgr._gw, "get_cached_client", return_value=None),
            patch.object(mgr._gw, "get_client", return_value=mock_client) as mock_gc,
        ):
            result = mgr._get_client_if_connected("gw1")
            mock_gc.assert_called_once_with(name="gw1")
        assert result is mock_client

    def test_get_client_raises_returns_none(self, mgr):
        """GatewayNotConnectedError returns None."""
        with (
            patch.object(mgr._gw, "get_cached_client", return_value=None),
            patch.object(mgr._gw, "get_client", side_effect=GatewayNotConnectedError),
        ):
            result = mgr._get_client_if_connected("gw1")
        assert result is None

    def test_get_client_grpc_error_returns_none(self, mgr):
        """grpc.RpcError returns None."""
        with (
            patch.object(mgr._gw, "get_cached_client", return_value=None),
            patch.object(mgr._gw, "get_client", side_effect=grpc.RpcError()),
        ):
            result = mgr._get_client_if_connected("gw1")
        assert result is None

    def test_get_client_oserror_returns_none(self, mgr):
        """OSError returns None."""
        with (
            patch.object(mgr._gw, "get_cached_client", return_value=None),
            patch.object(mgr._gw, "get_client", side_effect=OSError("fail")),
        ):
            result = mgr._get_client_if_connected("gw1")
        assert result is None


class TestListResourcesSafe:
    """Kill mutants in _list_resources_safe."""

    def test_returns_list_on_success(self, mgr):
        """Returns list when call succeeds."""
        fn = MagicMock(return_value=[{"name": "a"}])
        result = mgr._list_resources_safe(fn)
        assert result == [{"name": "a"}]
        fn.assert_called_once()

    def test_returns_empty_list_on_success(self, mgr):
        """Returns empty list when no resources."""
        fn = MagicMock(return_value=[])
        result = mgr._list_resources_safe(fn)
        assert result == []

    def test_returns_none_on_grpc_error(self, mgr):
        """grpc.RpcError returns None."""
        fn = MagicMock(side_effect=grpc.RpcError())
        result = mgr._list_resources_safe(fn)
        assert result is None

    def test_returns_none_on_oserror(self, mgr):
        """OSError returns None."""
        fn = MagicMock(side_effect=OSError("fail"))
        result = mgr._list_resources_safe(fn)
        assert result is None

    def test_returns_none_on_connection_error(self, mgr):
        """ConnectionError returns None."""
        fn = MagicMock(side_effect=ConnectionError("fail"))
        result = mgr._list_resources_safe(fn)
        assert result is None


# ─── Port management: additional exact tests ────────────────────────────────


class TestPortManagementExact:
    """Kill mutants in port management functions."""

    def test_get_port_for_gateway_returns_exact_value(self, mgr, config_dir):
        """Returns exact port from metadata."""
        gw_dir = config_dir / "gateways" / "gw1"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"gateway_port": 9999}))
        assert mgr._get_port_for_gateway("gw1") == 9999

    def test_get_port_for_gateway_no_port_key(self, mgr, config_dir):
        """Returns None when metadata has no gateway_port key."""
        gw_dir = config_dir / "gateways" / "gw1"
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"other": "value"}))
        assert mgr._get_port_for_gateway("gw1") is None

    def test_next_free_port_with_explicit_start(self, mgr, config_dir):
        """Explicit start parameter is respected."""
        assert mgr._next_free_port(start=9000) == 9000

    def test_next_free_port_skips_multiple(self, mgr, config_dir):
        """Skips multiple used ports."""
        for i, port in enumerate([8080, 8081, 8082]):
            gw_dir = config_dir / "gateways" / f"gw{i}"
            gw_dir.mkdir(parents=True)
            (gw_dir / "metadata.json").write_text(json.dumps({"gateway_port": port}))
        assert mgr._next_free_port() == 8083

    def test_next_free_port_overflow_exact_message(self, mgr, config_dir):
        """Exact error message on overflow."""
        with patch.object(mgr, "_get_used_ports", return_value=set(range(8080, 65536))):
            with pytest.raises(RuntimeError, match="No free ports available in valid range"):
                mgr._next_free_port()

    def test_get_used_ports_no_gateways_dir(self, mgr, config_dir):
        """Returns empty set when gateways dir doesn't exist."""
        result = mgr._get_used_ports()
        assert result == set()

    def test_get_used_ports_with_gateways(self, mgr, config_dir):
        """Returns set of all configured ports."""
        gw_dir = config_dir / "gateways"
        gw_dir.mkdir()
        for name, port in [("gw1", 8080), ("gw2", 8081)]:
            d = gw_dir / name
            d.mkdir()
            (d / "metadata.json").write_text(json.dumps({"gateway_port": port}))
        result = mgr._get_used_ports()
        assert result == {8080, 8081}

    def test_get_used_ports_skips_files(self, mgr, config_dir):
        """Skips non-directory entries."""
        gw_dir = config_dir / "gateways"
        gw_dir.mkdir()
        (gw_dir / "not-a-dir.txt").write_text("hello")
        d = gw_dir / "gw1"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({"gateway_port": 8080}))
        result = mgr._get_used_ports()
        assert result == {8080}

    def test_get_used_ports_skips_none_ports(self, mgr, config_dir):
        """Gateways without port config don't contribute."""
        gw_dir = config_dir / "gateways"
        gw_dir.mkdir()
        d = gw_dir / "gw1"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({"other": "val"}))
        result = mgr._get_used_ports()
        assert result == set()

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_find_port_blocker_skips_self(self, mock_status, mgr, config_dir):
        """Doesn't return self as blocker."""
        gw_dir = config_dir / "gateways" / GW
        gw_dir.mkdir(parents=True)
        (gw_dir / "metadata.json").write_text(json.dumps({"gateway_port": 8080}))
        mock_status.return_value = "running"
        result = mgr._find_port_blocker(GW, 8080)
        assert result is None
        mock_status.assert_not_called()

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_find_port_blocker_not_running(self, mock_status, mgr, config_dir):
        """Other gateway has same port but not running — not a blocker."""
        other_dir = config_dir / "gateways" / "other"
        other_dir.mkdir(parents=True)
        (other_dir / "metadata.json").write_text(json.dumps({"gateway_port": 8080}))
        mock_status.return_value = "exited"
        result = mgr._find_port_blocker(GW, 8080)
        assert result is None

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_find_port_blocker_different_port(self, mock_status, mgr, config_dir):
        """Other gateway running on different port — not a blocker."""
        other_dir = config_dir / "gateways" / "other"
        other_dir.mkdir(parents=True)
        (other_dir / "metadata.json").write_text(json.dumps({"gateway_port": 9090}))
        result = mgr._find_port_blocker(GW, 8080)
        assert result is None
        mock_status.assert_not_called()

    def test_find_port_blocker_no_gateways_dir(self, mgr, config_dir):
        """No gateways dir returns None."""
        result = mgr._find_port_blocker(GW, 8080)
        assert result is None

    @patch("shoreguard.services.local_gateway.LocalGatewayManager._get_container_status")
    def test_find_port_blocker_skips_files(self, mock_status, mgr, config_dir):
        """Skips non-directory entries in gateways dir."""
        gw_dir = config_dir / "gateways"
        gw_dir.mkdir()
        (gw_dir / "not-a-dir").write_text("file")
        result = mgr._find_port_blocker(GW, 8080)
        assert result is None


# ─── Container name ─────────────────────────────────────────────────────────


class TestContainerName:
    def test_container_name_format(self, mgr):
        """Exact container name format."""
        assert mgr._get_container_name("abc") == "openshell-cluster-abc"
        assert mgr._get_container_name("") == "openshell-cluster-"
        assert mgr._get_container_name("a-b-c") == "openshell-cluster-a-b-c"
