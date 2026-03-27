"""Gateway lifecycle management — Docker, XDG config, port conflicts, health."""

from __future__ import annotations

import json
import logging
import os
import shutil
import ssl
import subprocess
import threading
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
_clients_lock = threading.Lock()


def _reset_clients() -> None:
    """Clear all cached gateway clients. For test teardown only."""
    with _clients_lock:
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

        # Phase 1: read state under the lock
        with _clients_lock:
            entry = _clients.get(gw_name)
            if entry is None:
                entry = _ClientEntry()
                _clients[gw_name] = entry
            existing_client = entry.client

        # Phase 2: health-check existing client (blocking I/O, no lock)
        if existing_client is not None:
            try:
                existing_client.health()
                return existing_client
            except grpc.RpcError:
                logger.warning("Gateway '%s' connection lost, attempting reconnect...", gw_name)
                try:
                    existing_client.close()
                except Exception:
                    logger.debug("Error closing stale connection for '%s'", gw_name, exc_info=True)
                with _clients_lock:
                    entry.client = None
                    entry.backoff = 0.0

        # Phase 3: check backoff under the lock
        now = time.monotonic()
        with _clients_lock:
            if entry.backoff > 0 and (now - entry.last_attempt) < entry.backoff:
                raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")
            entry.last_attempt = now

        # Phase 4: attempt connection (blocking I/O, no lock)
        new_client = self._try_connect(gw_name)

        # Phase 5: write result under the lock
        with _clients_lock:
            if new_client is None:
                if entry.backoff == 0:
                    entry.backoff = _BACKOFF_MIN
                else:
                    entry.backoff = min(entry.backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
                raise GatewayNotConnectedError(f"Gateway '{gw_name}' not connected.")
            entry.client = new_client
            entry.backoff = 0.0
        return new_client

    def set_client(self, client: ShoreGuardClient | None, name: str | None = None) -> None:
        """Set or clear a client for the given gateway."""
        gw_name = name or self.get_active_name()
        if not gw_name:
            return
        with _clients_lock:
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
        with _clients_lock:
            if gw_name and gw_name in _clients:
                _clients[gw_name].backoff = 0.0
                _clients[gw_name].last_attempt = 0.0

    def _try_connect(self, name: str) -> ShoreGuardClient | None:
        """Attempt to create a client for a specific gateway."""
        try:
            client = ShoreGuardClient.from_active_cluster(cluster=name)
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug("Gateway '%s' connection failed: %s", name, e)
            return None
        try:
            client.health()
            logger.info("Connected to OpenShell gateway '%s'", name)
            return client
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError) as e:
            logger.debug("Gateway '%s' connection failed: %s", name, e)
            try:
                client.close()
            except Exception:
                logger.debug("Failed to close client for '%s'", name, exc_info=True)
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

        try:
            metadata = json.loads(metadata_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read metadata for '%s': %s", name, e)
            return {"name": name, "error": "Failed to read metadata"}
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
            "gpu": metadata.get("gpu", False),
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
            if not (entry / "metadata.json").exists():
                continue
            gw = self.read_metadata(entry.name)
            gw["active"] = entry.name == active_name

            container_status = self._get_container_status(entry.name)
            gw["container_status"] = container_status or "not_found"

            connected = False
            version = None
            with _clients_lock:
                cached = _clients.get(entry.name)
                cached_client = cached.client if cached else None
            if cached_client is not None:
                try:
                    health = cached_client.health()
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
        with _clients_lock:
            cached = _clients.get(gw_name)
            cached_client = cached.client if cached else None
        if cached_client is not None:
            try:
                health = cached_client.health()
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
            logger.debug("Health check failed for gateway '%s'", active_name)

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
                logger.debug("openshell --version check failed", exc_info=True)

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
        except (grpc.RpcError, OSError, ConnectionError, TimeoutError, ssl.SSLError) as e:
            logger.debug("Gateway '%s' select failed: %s", name, e, exc_info=True)
            err_msg = str(e)
            if isinstance(e, ssl.SSLError) or "SSL" in err_msg or "certificate" in err_msg.lower():
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
        logger.info("Starting gateway '%s'", gw_name)

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
        logger.info("Stopping gateway '%s'", gw_name)

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
        gw_name = name or self.get_active_name()
        if not gw_name:
            return {"success": False, "error": "No active gateway configured"}
        logger.info("Restarting gateway '%s'", gw_name)
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
            info = self.get_info(name)
            info["gpu"] = gpu
            return info

        return result

    def destroy(self, name: str, *, force: bool = False) -> dict[str, Any]:
        """Destroy a gateway and remove its configuration.

        Without force, refuses if sandboxes or providers still exist.
        With force=True, deletes all dependent resources first.
        """
        if not shutil.which("openshell"):
            return {"success": False, "error": "openshell CLI not found"}

        logger.info("Destroying gateway '%s'", name)

        # Check for dependent resources if gateway is connected
        client = self._get_client_if_connected(name)
        if client is not None:
            sandboxes = self._list_resources_safe(client.sandboxes.list)
            providers = self._list_resources_safe(client.providers.list)

            if (sandboxes or providers) and not force:
                details = []
                if sandboxes:
                    details.append(f"{len(sandboxes)} sandbox(es)")
                if providers:
                    details.append(f"{len(providers)} provider(s)")
                return {
                    "success": False,
                    "error": (
                        f"Gateway '{name}' still has {' and '.join(details)}. "
                        f"Use force=true to destroy everything."
                    ),
                    "sandboxes": [s.get("name", s.get("id", "")) for s in sandboxes],
                    "providers": [p.get("name", p.get("id", "")) for p in providers],
                }

            if force:
                for sb in sandboxes:
                    sb_name = sb.get("name", "")
                    if sb_name:
                        try:
                            client.sandboxes.delete(sb_name)
                        except Exception as e:
                            logger.warning(
                                "Failed to delete sandbox '%s' during gateway cleanup: %s",
                                sb_name,
                                e,
                            )

                for prov in providers:
                    prov_name = prov.get("name", "")
                    if prov_name:
                        try:
                            client.providers.delete(prov_name)
                        except Exception as e:
                            logger.warning(
                                "Failed to delete provider '%s' during gateway cleanup: %s",
                                prov_name,
                                e,
                            )

        active_name = self.get_active_name()
        if name == active_name:
            self.set_client(None)

        return self._run_openshell(
            ["gateway", "destroy", "--name", name],
            timeout=30,
        )

    def _get_client_if_connected(self, name: str) -> ShoreGuardClient | None:
        """Return the gRPC client for a gateway if connected, None otherwise."""
        with _clients_lock:
            cached = _clients.get(name)
            if cached and cached.client is not None:
                return cached.client
        try:
            active = self.get_active_name()
            if name == active:
                return self.get_client(name=name)
        except (GatewayNotConnectedError, grpc.RpcError, OSError):
            logger.debug(
                "Could not connect to gateway '%s' for resource listing",
                name,
                exc_info=True,
            )
        return None

    def _list_resources_safe(self, list_fn: Any) -> list[dict]:
        """Call a list function, returning [] on any error."""
        try:
            return list_fn()
        except (grpc.RpcError, OSError, ConnectionError):
            logger.debug("Failed to list resources via %s", list_fn, exc_info=True)
            return []

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
