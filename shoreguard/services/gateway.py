"""Gateway lifecycle management — Docker, XDG config, port conflicts, health."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any

import grpc

from shoreguard.client import ShoreGuardClient
from shoreguard.config import openshell_config_dir
from shoreguard.exceptions import GatewayNotConnectedError

logger = logging.getLogger("shoreguard")

# ─── Connection state ────────────────────────────────────────────────────────

_BACKOFF_MIN = 5.0
_BACKOFF_MAX = 60.0
_BACKOFF_FACTOR = 2.0


class _ClientEntry:
    """Per-gateway connection state with backoff."""

    __slots__ = ("client", "last_attempt", "backoff")

    def __init__(self) -> None:
        self.client: ShoreGuardClient | None = None
        self.last_attempt: float = 0.0
        self.backoff: float = 0.0


_clients: dict[str, _ClientEntry] = {}


def _reset_clients() -> None:
    """Clear all cached gateway clients. For test teardown only."""
    _clients.clear()


def _derive_status(connected: bool, container_status: str | None) -> str:
    """Derive a single status string from connection and container state."""
    if connected:
        return "connected"
    if container_status == "running":
        return "running"
    if container_status in ("exited", "created", "dead"):
        return "stopped"
    return "offline"


# ─── Gateway Service ─────────────────────────────────────────────────────────


class GatewayService:
    """Gateway lifecycle management.

    Handles Docker container operations, XDG config discovery,
    port conflict detection, and client connection management.
    Shared by Web UI (via FastAPI routes) and TUI.
    """

    # ── Connection management ─────────────────────────────────────────────

    def get_client(self, name: str | None = None) -> ShoreGuardClient:
        """Return a client for the given gateway, attempting reconnect with backoff.

        If *name* is None, falls back to the active gateway from config.
        """
        gw_name = name or self.get_active_name()
        if not gw_name:
            raise GatewayNotConnectedError("No gateway specified or configured.")

        entry = _clients.get(gw_name)
        if entry is None:
            entry = _ClientEntry()
            _clients[gw_name] = entry

        if entry.client is not None:
            try:
                entry.client.health()
                return entry.client
            except grpc.RpcError:
                logger.info("Gateway '%s' connection lost, attempting reconnect...", gw_name)
                try:
                    entry.client.close()
                except Exception:
                    pass
                entry.client = None
                entry.backoff = 0.0

        now = time.monotonic()
        if entry.backoff > 0 and (now - entry.last_attempt) < entry.backoff:
            raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")

        entry.last_attempt = now
        entry.client = self._try_connect(gw_name)
        if entry.client is None:
            if entry.backoff == 0:
                entry.backoff = _BACKOFF_MIN
            else:
                entry.backoff = min(entry.backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
            raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")
        entry.backoff = 0.0
        return entry.client

    def set_client(self, client: ShoreGuardClient | None, name: str | None = None) -> None:
        """Set or clear a client for the given gateway."""
        gw_name = name or self.get_active_name()
        if not gw_name:
            return
        if client is None:
            _clients.pop(gw_name, None)
        else:
            entry = _clients.get(gw_name)
            if entry is None:
                entry = _ClientEntry()
                _clients[gw_name] = entry
            entry.client = client
            entry.backoff = 0.0

    def reset_backoff(self, name: str | None = None) -> None:
        """Reset connection backoff for a gateway."""
        gw_name = name or self.get_active_name()
        if gw_name and gw_name in _clients:
            _clients[gw_name].backoff = 0.0
            _clients[gw_name].last_attempt = 0.0

    def _try_connect(self, name: str) -> ShoreGuardClient | None:
        """Attempt to create a client for a specific gateway."""
        try:
            client = ShoreGuardClient.from_active_cluster(cluster=name)
            client.health()
            logger.info("Connected to OpenShell gateway '%s'", name)
            return client
        except Exception as e:
            logger.debug("Gateway '%s' connection failed: %s", name, e)
            return None

    # ── Gateway discovery ─────────────────────────────────────────────────

    def get_active_name(self) -> str | None:
        """Read the active gateway name from config."""
        active_file = openshell_config_dir() / "active_gateway"
        if not active_file.exists():
            return None
        name = active_file.read_text().strip()
        return name or None

    def read_metadata(self, name: str) -> dict[str, Any]:
        """Read metadata for a specific gateway."""
        metadata_file = openshell_config_dir() / "gateways" / name / "metadata.json"
        if not metadata_file.exists():
            return {"name": name, "error": "Metadata file not found"}

        metadata = json.loads(metadata_file.read_text())
        auth_mode = metadata.get("auth_mode")
        if auth_mode == "cloudflare_jwt":
            gw_type = "cloud"
        elif metadata.get("is_remote"):
            gw_type = "remote"
        else:
            gw_type = "local"

        return {
            "name": name,
            "endpoint": metadata.get("gateway_endpoint", ""),
            "is_remote": metadata.get("is_remote", False),
            "port": metadata.get("gateway_port"),
            "type": gw_type,
            "auth_mode": auth_mode,
            "remote_host": metadata.get("remote_host"),
        }

    # ── List & Info ───────────────────────────────────────────────────────

    def list_all(self) -> list[dict[str, Any]]:
        """List all configured gateways with metadata, container and connection status."""
        gateways_dir = openshell_config_dir() / "gateways"
        if not gateways_dir.exists():
            return []

        active_name = self.get_active_name()
        result = []

        for entry in sorted(gateways_dir.iterdir()):
            if not entry.is_dir():
                continue
            gw = self.read_metadata(entry.name)
            gw["active"] = entry.name == active_name

            container_status = self._get_container_status(entry.name)
            gw["container_status"] = container_status or "not_found"

            connected = False
            version = None
            cached = _clients.get(entry.name)
            if cached and cached.client is not None:
                try:
                    health = cached.client.health()
                    connected = True
                    version = health.get("version")
                except grpc.RpcError:
                    self.set_client(None, name=entry.name)

            gw["connected"] = connected
            if version:
                gw["version"] = version
            gw["status"] = _derive_status(connected, gw["container_status"])
            result.append(gw)

        return result

    def get_info(self, name: str | None = None) -> dict[str, Any]:
        """Get detailed info for a gateway (active if name is None)."""
        gw_name = name or self.get_active_name()
        if not gw_name:
            return {"configured": False, "error": "No active gateway configured"}

        metadata = self.read_metadata(gw_name)
        metadata["configured"] = True
        metadata["active"] = gw_name == self.get_active_name()

        connected = False
        version = None
        cached = _clients.get(gw_name)
        if cached and cached.client is not None:
            try:
                health = cached.client.health()
                connected = True
                version = health.get("version")
            except grpc.RpcError:
                self.set_client(None, name=gw_name)

        metadata["connected"] = connected
        if version:
            metadata["version"] = version

        container_status = self._get_container_status(gw_name)
        metadata["container_status"] = container_status or "not_found"
        metadata["status"] = _derive_status(connected, metadata["container_status"])
        return metadata

    def health(self) -> dict[str, Any]:
        """Combined health + gateway info in one call."""
        active_name = self.get_active_name()
        result: dict[str, Any] = {
            "connected": False,
            "gateway_name": active_name,
        }

        try:
            client = self.get_client()
            health = client.health()
            result["connected"] = True
            result["version"] = health.get("version")
            result["health_status"] = health.get("status")
        except (grpc.RpcError, GatewayNotConnectedError):
            pass

        return result

    def get_config(self) -> dict[str, Any]:
        """Fetch the global gateway configuration via gRPC."""
        client = self.get_client()
        return client.get_gateway_config()

    # ── Diagnostics ───────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        """Check Docker availability, daemon status, and permissions."""
        result: dict[str, Any] = {
            "docker_installed": False,
            "docker_daemon_running": False,
            "docker_accessible": False,
            "docker_version": None,
            "docker_error": None,
            "openshell_installed": False,
            "openshell_version": None,
            "user": os.environ.get("USER", "unknown"),
        }

        if shutil.which("docker"):
            result["docker_installed"] = True
            try:
                proc = subprocess.run(
                    ["docker", "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0:
                    result["docker_daemon_running"] = True
                    result["docker_accessible"] = True
                    result["docker_version"] = proc.stdout.strip()
                else:
                    stderr = proc.stderr.strip()
                    if "permission denied" in stderr.lower():
                        result["docker_error"] = "Permission denied"
                        result["docker_daemon_running"] = True
                    elif "is the docker daemon running" in stderr.lower():
                        result["docker_error"] = "Docker daemon is not running"
                    elif "not responding" in stderr.lower():
                        result["docker_error"] = "Docker daemon is not responding"
                    else:
                        result["docker_error"] = stderr
            except (subprocess.SubprocessError, OSError) as e:
                result["docker_error"] = str(e)

        try:
            proc = subprocess.run(
                ["groups"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            groups = proc.stdout.strip().split() if proc.returncode == 0 else []
            result["user_groups"] = groups
            result["in_docker_group"] = "docker" in groups
        except (subprocess.SubprocessError, OSError):
            result["user_groups"] = []
            result["in_docker_group"] = False

        if shutil.which("openshell"):
            result["openshell_installed"] = True
            try:
                proc = subprocess.run(
                    ["openshell", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0:
                    result["openshell_version"] = proc.stdout.strip()
            except (subprocess.SubprocessError, OSError):
                pass

        return result

    # ── Lifecycle actions ─────────────────────────────────────────────────

    def select(self, name: str) -> dict[str, Any]:
        """Set a gateway as active and reconnect."""
        gateways_dir = openshell_config_dir() / "gateways" / name
        if not gateways_dir.exists():
            return {"success": False, "error": f"Gateway '{name}' not found"}

        container_status = self._get_container_status(name)

        if container_status != "running":
            self._write_active_gateway(name)
            status_msg = container_status or "not found"
            return {
                "success": True,
                "connected": False,
                "warning": (f"Gateway container is {status_msg}. Start it first to connect."),
            }

        self._write_active_gateway(name)

        try:
            self.get_client()
            return {"success": True, "connected": True}
        except Exception as e:
            err_msg = str(e)
            if "SSL" in err_msg or "certificate" in err_msg.lower():
                return {
                    "success": True,
                    "connected": False,
                    "warning": (
                        "Connected to container but TLS handshake failed. "
                        "The gateway may need to be restarted to "
                        "regenerate certificates."
                    ),
                }
            return {"success": True, "connected": False}

    def start(self, name: str | None = None) -> dict[str, Any]:
        """Start a gateway by name (or active if None)."""
        gw_name = name or self.get_active_name()
        if not gw_name:
            return {"success": False, "error": "No active gateway configured"}

        if not self._check_docker_daemon():
            return {
                "success": False,
                "error": "Docker daemon is not running. "
                "Start it first: sudo systemctl start docker",
            }

        status = self._get_container_status(gw_name)

        if status == "running":
            active_name = self.get_active_name()
            if name is None or name == active_name:
                try:
                    self.get_client()
                except GatewayNotConnectedError:
                    pass
            return {"success": True, "output": "Gateway is already running"}

        if status in ("exited", "created", "dead"):
            port = self._get_port_for_gateway(gw_name)
            if port:
                blocker = self._find_port_blocker(gw_name, port)
                if blocker:
                    return {
                        "success": False,
                        "error": (
                            f"Port {port} is already in use by gateway "
                            f'"{blocker}". Stop it first, or recreate '
                            f'"{gw_name}" on a different port.'
                        ),
                    }

            result = self._docker_start_container(gw_name)
            if result["success"]:
                self.reset_backoff()
                active_name = self.get_active_name()
                if name is None or name == active_name:
                    for _ in range(10):
                        time.sleep(2)
                        try:
                            self.get_client()
                            break
                        except GatewayNotConnectedError:
                            pass
            return result

        if not shutil.which("openshell"):
            return {"success": False, "error": "openshell CLI not found"}

        args = ["gateway", "start", "--name", gw_name]
        result = self._run_openshell(args, timeout=600)

        if result["success"]:
            active_name = self.get_active_name()
            if name is None or name == active_name:
                try:
                    self.get_client()
                except GatewayNotConnectedError:
                    pass

        return result

    def stop(self, name: str | None = None) -> dict[str, Any]:
        """Stop a gateway by name (or active if None)."""
        gw_name = name or self.get_active_name()
        if not gw_name:
            return {"success": False, "error": "No active gateway configured"}

        status = self._get_container_status(gw_name)
        if status != "running":
            return {"success": True, "output": "Gateway is already stopped"}

        result = self._docker_stop_container(gw_name)

        if result["success"]:
            active_name = self.get_active_name()
            if name is None or name == active_name:
                self.set_client(None)

        return result

    def restart(self, name: str | None = None) -> dict[str, Any]:
        """Restart a gateway (stop + start)."""
        self.stop(name=name)
        return self.start(name=name)

    def create(
        self,
        name: str,
        port: int | None = None,
        *,
        remote_host: str | None = None,
        gpu: bool = False,
    ) -> dict[str, Any]:
        """Create a new gateway."""
        if not shutil.which("openshell"):
            return {"success": False, "error": "openshell CLI not found"}

        if not self._check_docker_daemon():
            return {
                "success": False,
                "error": "Docker daemon is not running. "
                "Start it first: sudo systemctl start docker",
            }

        if port and port > 0:
            blocker = self._find_port_blocker(name, port)
            if blocker:
                return {
                    "success": False,
                    "error": (
                        f"Port {port} is already configured for gateway "
                        f'"{blocker}". Choose a different port or stop '
                        f"that gateway first."
                    ),
                }
        else:
            port = self._next_free_port()

        args = ["gateway", "start", "--name", name, "--port", str(port)]
        if remote_host:
            args.extend(["--remote", remote_host])
        if gpu:
            args.append("--gpu")

        result = self._run_openshell(args, timeout=600)

        if result["success"]:
            self._write_active_gateway(name)
            try:
                self.get_client()
            except GatewayNotConnectedError:
                pass

        return result

    def destroy(self, name: str) -> dict[str, Any]:
        """Destroy a gateway and remove its configuration."""
        if not shutil.which("openshell"):
            return {"success": False, "error": "openshell CLI not found"}

        active_name = self.get_active_name()
        if name == active_name:
            self.set_client(None)

        return self._run_openshell(
            ["gateway", "destroy", "--name", name],
            timeout=30,
        )

    # ── Docker helpers ────────────────────────────────────────────────────

    def _get_container_name(self, gateway_name: str) -> str:
        return f"openshell-cluster-{gateway_name}"

    def _get_container_status(self, gateway_name: str) -> str | None:
        container = self._get_container_name(gateway_name)
        try:
            proc = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", container],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
        return None

    def _docker_start_container(self, gateway_name: str) -> dict[str, Any]:
        container = self._get_container_name(gateway_name)
        try:
            proc = subprocess.run(
                ["docker", "start", container],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return {"success": True, "output": f"Container {container} started"}
            err = proc.stderr.strip() or f"Exit code {proc.returncode}"
            return {"success": False, "error": err}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Docker start timed out (30s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _docker_stop_container(self, gateway_name: str) -> dict[str, Any]:
        container = self._get_container_name(gateway_name)
        try:
            proc = subprocess.run(
                ["docker", "stop", container],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return {"success": True, "output": f"Container {container} stopped"}
            err = proc.stderr.strip() or f"Exit code {proc.returncode}"
            return {"success": False, "error": err}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Docker stop timed out (30s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _check_docker_daemon(self) -> bool:
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    # ── Port management ───────────────────────────────────────────────────

    def _get_port_for_gateway(self, gateway_name: str) -> int | None:
        metadata_file = openshell_config_dir() / "gateways" / gateway_name / "metadata.json"
        if not metadata_file.exists():
            return None
        try:
            metadata = json.loads(metadata_file.read_text())
            return metadata.get("gateway_port")
        except (json.JSONDecodeError, OSError):
            return None

    def _find_port_blocker(self, gateway_name: str, port: int) -> str | None:
        gateways_dir = openshell_config_dir() / "gateways"
        if not gateways_dir.exists():
            return None
        for entry in gateways_dir.iterdir():
            if not entry.is_dir() or entry.name == gateway_name:
                continue
            other_port = self._get_port_for_gateway(entry.name)
            if other_port == port:
                status = self._get_container_status(entry.name)
                if status == "running":
                    return entry.name
        return None

    def _get_used_ports(self) -> set[int]:
        gateways_dir = openshell_config_dir() / "gateways"
        if not gateways_dir.exists():
            return set()
        ports: set[int] = set()
        for entry in gateways_dir.iterdir():
            if not entry.is_dir():
                continue
            port = self._get_port_for_gateway(entry.name)
            if port:
                ports.add(port)
        return ports

    def _next_free_port(self, start: int = 8080) -> int:
        used = self._get_used_ports()
        port = start
        while port in used:
            port += 1
        return port

    # ── OpenShell CLI ─────────────────────────────────────────────────────

    def _run_openshell(self, args: list[str], *, timeout: int = 30) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                ["openshell", *args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = proc.stdout.strip()
            error_output = proc.stderr.strip()

            if proc.returncode == 0:
                return {"success": True, "output": output or error_output}

            error_msg = error_output or output or f"Exit code {proc.returncode}"
            return {"success": False, "error": error_msg}

        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Internal helpers ──────────────────────────────────────────────────

    def _write_active_gateway(self, name: str) -> None:
        """Switch the active gateway file (for OpenShell CLI compat)."""
        active_file = openshell_config_dir() / "active_gateway"
        active_file.write_text(name)
        logger.info("Set active gateway to '%s'", name)
        self.reset_backoff(name)


# Module-level singleton
gateway_service = GatewayService()
