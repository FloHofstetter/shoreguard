"""Local gateway lifecycle — Docker containers, openshell CLI, port management.

Only used when SHOREGUARD_LOCAL_MODE=1 or for backward-compatible v0.2 workflows.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Any

import grpc

from shoreguard.client import ShoreGuardClient
from shoreguard.config import openshell_config_dir
from shoreguard.exceptions import GatewayNotConnectedError
from shoreguard.settings import get_settings

from .gateway import GatewayService

logger = logging.getLogger(__name__)


class LocalGatewayManager:
    """Docker and openshell CLI lifecycle for locally-managed gateways.

    Args:
        gateway_service: Gateway service for connection management.
    """

    def __init__(self, gateway_service: GatewayService) -> None:  # noqa: D107
        self._gw = gateway_service

    # ── Diagnostics ───────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        """Check Docker availability, daemon status, and permissions.

        Returns:
            dict[str, Any]: Diagnostic information about Docker and openshell.
        """
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
        except subprocess.SubprocessError, OSError:
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
            except subprocess.SubprocessError, OSError:
                logger.debug("openshell --version check failed", exc_info=True)

        return result

    # ── Lifecycle actions ─────────────────────────────────────────────────

    def start(self, name: str) -> dict[str, Any]:
        """Start a gateway by name.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Result with success status and output or error.
        """
        gw_name = name
        logger.info("Starting gateway '%s'", gw_name)

        if not self._check_docker_daemon():
            logger.error("Cannot start gateway '%s': Docker daemon is not running", gw_name)
            return {
                "success": False,
                "error": "Docker daemon is not running. "
                "Start it first: sudo systemctl start docker",
            }

        status = self._get_container_status(gw_name)

        if status == "running":
            try:
                self._gw.get_client(name=gw_name)
            except GatewayNotConnectedError:
                logger.debug("Gateway '%s' running but not yet connectable", gw_name)
            return {"success": True, "output": "Gateway is already running"}

        if status in ("exited", "created", "dead"):
            port = self._get_port_for_gateway(gw_name)
            if port:
                blocker = self._find_port_blocker(gw_name, port)
                if blocker:
                    logger.warning(
                        "Port conflict when starting '%s': port %d in use by '%s'",
                        gw_name,
                        port,
                        blocker,
                    )
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
                self._gw.reset_backoff(name=gw_name)
                lgw = get_settings().local_gw
                for attempt in range(lgw.startup_retries):
                    time.sleep(lgw.startup_sleep)
                    try:
                        self._gw.get_client(name=gw_name)
                        break
                    except GatewayNotConnectedError:
                        logger.debug(
                            "Waiting for gateway '%s' to become connectable (attempt %d/10)",
                            gw_name,
                            attempt + 1,
                        )
                else:
                    logger.warning(
                        "Gateway '%s' started but not connectable after 10 attempts",
                        gw_name,
                    )
            return result

        if not shutil.which("openshell"):
            logger.error("Cannot start gateway '%s': openshell CLI not found", gw_name)
            return {"success": False, "error": "openshell CLI not found"}

        args = ["gateway", "start", "--name", gw_name]
        result = self._run_openshell(args, timeout=int(get_settings().local_gw.openshell_timeout))

        if result["success"]:
            try:
                self._gw.get_client(name=gw_name)
            except GatewayNotConnectedError:
                logger.debug("Gateway '%s' started but not yet connectable", gw_name)

        return result

    def stop(self, name: str) -> dict[str, Any]:
        """Stop a gateway by name.

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Result with success status and output or error.
        """
        gw_name = name
        logger.info("Stopping gateway '%s'", gw_name)

        status = self._get_container_status(gw_name)
        if status != "running":
            return {"success": True, "output": "Gateway is already stopped"}

        result = self._docker_stop_container(gw_name)

        if result["success"]:
            self._gw.set_client(None, name=gw_name)

        return result

    def restart(self, name: str) -> dict[str, Any]:
        """Restart a gateway (stop + start).

        Args:
            name: Gateway name.

        Returns:
            dict[str, Any]: Result from the start step.
        """
        logger.info("Restarting gateway '%s'", name)
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
        """Create a new gateway via openshell CLI.

        Args:
            name: Gateway name.
            port: Port number, or None for auto-selection.
            remote_host: Remote host for the gateway.
            gpu: Whether to enable GPU support.

        Returns:
            dict[str, Any]: Gateway info or error result.
        """
        if not shutil.which("openshell"):
            logger.error("Cannot create gateway '%s': openshell CLI not found", name)
            return {"success": False, "error": "openshell CLI not found"}

        if not self._check_docker_daemon():
            logger.error("Cannot create gateway '%s': Docker daemon is not running", name)
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

        result = self._run_openshell(args, timeout=int(get_settings().local_gw.openshell_timeout))

        if result["success"]:
            try:
                self._gw.get_client(name=name)
            except GatewayNotConnectedError:
                logger.debug("Gateway '%s' created but not yet connectable", name)
            info = self._gw.get_info(name)
            info["gpu"] = gpu
            return info

        return result

    def destroy(self, name: str, *, force: bool = False) -> dict[str, Any]:
        """Destroy a gateway and remove its configuration.

        Args:
            name: Gateway name.
            force: Force destruction even if resources still exist.

        Returns:
            dict[str, Any]: Result with success status.
        """
        if not shutil.which("openshell"):
            logger.error("Cannot destroy gateway '%s': openshell CLI not found", name)
            return {"success": False, "error": "openshell CLI not found"}

        logger.info("Destroying gateway '%s'", name)

        client = self._get_client_if_connected(name)
        if client is not None:
            sandboxes = self._list_resources_safe(client.sandboxes.list)
            providers = self._list_resources_safe(client.providers.list)

            if sandboxes is None or providers is None:
                if not force:
                    return {
                        "success": False,
                        "error": (
                            f"Could not list resources for gateway '{name}'. "
                            f"Use force=true to destroy anyway."
                        ),
                    }
            elif (sandboxes or providers) and not force:
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
                for sb in sandboxes or []:
                    sb_name = sb.get("name", "")
                    if sb_name:
                        try:
                            client.sandboxes.delete(sb_name)
                        except (grpc.RpcError, OSError, ConnectionError) as e:
                            logger.warning(
                                "Failed to delete sandbox '%s' during gateway cleanup: %s",
                                sb_name,
                                e,
                            )

                for prov in providers or []:
                    prov_name = prov.get("name", "")
                    if prov_name:
                        try:
                            client.providers.delete(prov_name)
                        except (grpc.RpcError, OSError, ConnectionError) as e:
                            logger.warning(
                                "Failed to delete provider '%s' during gateway cleanup: %s",
                                prov_name,
                                e,
                            )

        self._gw.set_client(None, name=name)

        return self._run_openshell(
            ["gateway", "destroy", "--name", name],
            timeout=int(get_settings().local_gw.docker_timeout),
        )

    # ── Docker helpers ────────────────────────────────────────────────────

    def _get_container_name(self, gateway_name: str) -> str:
        """Return the Docker container name for a gateway.

        Args:
            gateway_name: Gateway name.

        Returns:
            str: Container name.
        """
        return f"openshell-cluster-{gateway_name}"

    def _get_container_status(self, gateway_name: str) -> str | None:
        """Get the Docker container status for a gateway.

        Args:
            gateway_name: Gateway name.

        Returns:
            str | None: Container status string, or None if not found.
        """
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
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Failed to inspect container for '%s': %s", gateway_name, e)
        return None

    def _docker_start_container(self, gateway_name: str) -> dict[str, Any]:
        """Start a Docker container for a gateway.

        Args:
            gateway_name: Gateway name.

        Returns:
            dict[str, Any]: Result with success status.
        """
        container = self._get_container_name(gateway_name)
        try:
            docker_timeout = int(get_settings().local_gw.docker_timeout)
            proc = subprocess.run(
                ["docker", "start", container],
                capture_output=True,
                text=True,
                timeout=docker_timeout,
            )
            if proc.returncode == 0:
                logger.info("Docker container started for '%s'", gateway_name)
                return {"success": True, "output": f"Container {container} started"}
            err = proc.stderr.strip() or f"Exit code {proc.returncode}"
            logger.warning("Docker start failed for '%s': %s", gateway_name, err)
            return {"success": False, "error": err}
        except subprocess.TimeoutExpired:
            logger.warning("Docker start timed out for '%s' (30s)", gateway_name)
            return {"success": False, "error": "Docker start timed out (30s)"}
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("Docker start error for '%s': %s", gateway_name, e, exc_info=True)
            return {"success": False, "error": str(e)}

    def _docker_stop_container(self, gateway_name: str) -> dict[str, Any]:
        """Stop a Docker container for a gateway.

        Args:
            gateway_name: Gateway name.

        Returns:
            dict[str, Any]: Result with success status.
        """
        container = self._get_container_name(gateway_name)
        try:
            docker_timeout = int(get_settings().local_gw.docker_timeout)
            proc = subprocess.run(
                ["docker", "stop", container],
                capture_output=True,
                text=True,
                timeout=docker_timeout,
            )
            if proc.returncode == 0:
                logger.info("Docker container stopped for '%s'", gateway_name)
                return {"success": True, "output": f"Container {container} stopped"}
            err = proc.stderr.strip() or f"Exit code {proc.returncode}"
            logger.warning("Docker stop failed for '%s': %s", gateway_name, err)
            return {"success": False, "error": err}
        except subprocess.TimeoutExpired:
            logger.warning("Docker stop timed out for '%s' (30s)", gateway_name)
            return {"success": False, "error": "Docker stop timed out (30s)"}
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("Docker stop error for '%s': %s", gateway_name, e, exc_info=True)
            return {"success": False, "error": str(e)}

    def _check_docker_daemon(self) -> bool:
        """Check if the Docker daemon is running and accessible.

        Returns:
            bool: True if the daemon is running.
        """
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return proc.returncode == 0
        except subprocess.SubprocessError, OSError:
            logger.debug("Docker daemon check failed", exc_info=True)
            return False

    # ── Port management ───────────────────────────────────────────────────

    def _get_port_for_gateway(self, gateway_name: str) -> int | None:
        """Read the configured port for a gateway from its metadata file.

        Args:
            gateway_name: Gateway name.

        Returns:
            int | None: Port number, or None if not found.
        """
        metadata_file = openshell_config_dir() / "gateways" / gateway_name / "metadata.json"
        if not metadata_file.exists():
            return None
        try:
            metadata = json.loads(metadata_file.read_text())
            return metadata.get("gateway_port")
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to read metadata for '%s': %s", gateway_name, e)
            return None

    def _find_port_blocker(self, gateway_name: str, port: int) -> str | None:
        """Find another running gateway that is using the given port.

        Args:
            gateway_name: Gateway name to exclude from the check.
            port: Port number to check.

        Returns:
            str | None: Name of the blocking gateway, or None.
        """
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
        """Collect all ports configured across all gateways.

        Returns:
            set[int]: Set of port numbers in use.
        """
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

    def _next_free_port(self, start: int | None = None) -> int:
        """Find the next free port starting from the given number.

        Args:
            start: Port number to start searching from.

        Returns:
            int: First available port number.

        Raises:
            RuntimeError: If no free port is found in valid range.
        """
        if start is None:
            start = get_settings().local_gw.starting_port
        used = self._get_used_ports()
        port = start
        while port in used:
            port += 1
            if port > 65535:
                raise RuntimeError("No free ports available in valid range (8080-65535)")
        return port

    # ── OpenShell CLI ─────────────────────────────────────────────────────

    def _run_openshell(self, args: list[str], *, timeout: int = 30) -> dict[str, Any]:
        """Run an openshell CLI command and return the result.

        Args:
            args: Command arguments (without the "openshell" prefix).
            timeout: Command timeout in seconds.

        Returns:
            dict[str, Any]: Result with success status and output or error.
        """
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
            logger.warning("openshell %s failed: %s", " ".join(args), error_msg)
            return {"success": False, "error": error_msg}

        except subprocess.TimeoutExpired:
            logger.error("openshell %s timed out after %ds", " ".join(args), timeout)
            return {"success": False, "error": f"Command timed out ({timeout}s)"}
        except (subprocess.SubprocessError, OSError) as e:
            logger.error("openshell %s failed: %s", " ".join(args), e, exc_info=True)
            return {"success": False, "error": str(e)}

    # ── Internal helpers ──────────────────────────────────────────────────

    def _get_client_if_connected(self, name: str) -> ShoreGuardClient | None:
        """Return a connected client for the gateway, or None.

        Args:
            name: Gateway name.

        Returns:
            ShoreGuardClient | None: Connected client, or None.
        """
        cached = self._gw.get_cached_client(name)
        if cached is not None:
            return cached
        try:
            return self._gw.get_client(name=name)
        except GatewayNotConnectedError, grpc.RpcError, OSError:
            logger.debug(
                "Could not connect to gateway '%s' for resource listing",
                name,
                exc_info=True,
            )
        return None

    def _list_resources_safe(self, list_fn: Callable[[], list[dict]]) -> list[dict] | None:
        """Return resource list, or None if the listing call failed.

        Args:
            list_fn: Callable that returns a list of resource dicts.

        Returns:
            list[dict] | None: Resource list, or None on failure.
        """
        try:
            return list_fn()
        except grpc.RpcError, OSError, ConnectionError:
            logger.debug("Failed to list resources via %s", list_fn, exc_info=True)
            return None


# Module-level reference — set during app lifespan when SHOREGUARD_LOCAL_MODE=1
local_gateway_manager: LocalGatewayManager | None = None
