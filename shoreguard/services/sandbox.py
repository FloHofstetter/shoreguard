"""Sandbox lifecycle operations including create-with-presets workflow."""

from __future__ import annotations

import logging
import shlex
import time
from typing import Any

import grpc

from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import SandboxError, ValidationError, friendly_grpc_error
from shoreguard.services.policy import PolicyService
from shoreguard.services.sandbox_meta import _UNSET, SandboxMetaStore

logger = logging.getLogger(__name__)


class SandboxService:
    """Sandbox operations shared by Web UI and TUI.

    Provides higher-level workflows like create-with-presets
    that were previously implemented in browser JS.

    Args:
        client: OpenShell gRPC client instance.
        meta_store: Optional metadata store for labels/description.
    """

    def __init__(  # noqa: D107
        self,
        client: ShoreGuardClient,
        meta_store: SandboxMetaStore | None = None,
    ) -> None:
        self._client = client
        self._policy = PolicyService(client)
        self._meta = meta_store

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        gateway_name: str | None = None,
        labels_filter: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """List all sandboxes with merged metadata.

        Args:
            limit: Maximum number of sandboxes to return.
            offset: Number of sandboxes to skip.
            gateway_name: Gateway name for metadata lookup.
            labels_filter: Filter sandboxes by label key-value pairs.

        Returns:
            list[dict[str, Any]]: Sandbox records with metadata.
        """
        sandboxes = self._client.sandboxes.list(limit=limit, offset=offset)
        if self._meta and gateway_name:
            all_meta = self._meta.list_for_gateway(gateway_name)
            for sb in sandboxes:
                name = sb.get("name", "")
                meta = all_meta.get(name)
                sb["description"] = meta["description"] if meta else None
                sb["labels"] = meta["labels"] if meta else {}
            if labels_filter:
                sandboxes = [
                    sb
                    for sb in sandboxes
                    if all(sb.get("labels", {}).get(k) == v for k, v in labels_filter.items())
                ]
        return sandboxes

    def get(self, name: str, *, gateway_name: str | None = None) -> dict[str, Any]:
        """Get a sandbox by name with merged metadata.

        Args:
            name: Sandbox name.
            gateway_name: Gateway name for metadata lookup.

        Returns:
            dict[str, Any]: Sandbox record with metadata.
        """
        sb = self._client.sandboxes.get(name)
        if self._meta and gateway_name:
            meta = self._meta.get(gateway_name, name)
            sb["description"] = meta["description"] if meta else None
            sb["labels"] = meta["labels"] if meta else {}
        return sb

    def delete(self, name: str, *, gateway_name: str | None = None) -> bool:
        """Delete a sandbox by name and clean up metadata.

        Args:
            name: Sandbox name.
            gateway_name: Gateway name for metadata cleanup.

        Returns:
            bool: True if the sandbox was deleted.
        """
        deleted = self._client.sandboxes.delete(name)
        if deleted and self._meta and gateway_name:
            self._meta.delete(gateway_name, name)
        return deleted

    def exec(
        self,
        name: str,
        command: str | list[str],
        *,
        workdir: str = "",
        env: dict[str, str] | None = None,
        timeout_seconds: int = 0,
        tty: bool = False,
    ) -> dict[str, Any]:
        """Execute a command inside a sandbox.

        Accepts command as a raw string (parsed with shlex) or list.

        Args:
            name: Sandbox name.
            command: Command as a string or list of arguments.
            workdir: Working directory inside the sandbox.
            env: Environment variables to set.
            timeout_seconds: Execution timeout (0 for no timeout).
            tty: Allocate a TTY for the command (for interactive programs).

        Returns:
            dict[str, Any]: Execution result with stdout, stderr, exit code.

        Raises:
            ValidationError: If the command string has invalid syntax.
        """
        sandbox = self._client.sandboxes.get(name)
        if isinstance(command, str):
            try:
                command = shlex.split(command)
            except ValueError as e:
                raise ValidationError(f"Invalid command syntax: {e}") from e
        return self._client.sandboxes.exec(
            sandbox["id"],
            command,
            workdir=workdir,
            env=env,
            timeout_seconds=timeout_seconds,
            tty=tty,
        )

    def get_logs(
        self,
        name: str,
        *,
        lines: int = 200,
        since_ms: int = 0,
        sources: list[str] | None = None,
        min_level: str = "",
    ) -> list[dict[str, Any]]:
        """Fetch recent logs from a sandbox.

        Args:
            name: Sandbox name.
            lines: Maximum number of log lines to return.
            since_ms: Only return logs after this epoch millisecond timestamp.
            sources: Filter by log source names.
            min_level: Minimum log level filter.

        Returns:
            list[dict[str, Any]]: Log entries.
        """
        sandbox = self._client.sandboxes.get(name)
        return self._client.sandboxes.get_logs(
            sandbox["id"],
            lines=lines,
            since_ms=since_ms,
            sources=sources,
            min_level=min_level,
        )

    def create_ssh_session(self, name: str) -> dict[str, Any]:
        """Create an SSH session for a sandbox, resolving name to ID.

        Args:
            name: Sandbox name.

        Returns:
            dict[str, Any]: SSH session details including token.
        """
        sandbox = self._client.sandboxes.get(name)
        return self._client.sandboxes.create_ssh_session(sandbox["id"])

    def revoke_ssh_session(self, token: str) -> bool:
        """Revoke an active SSH session by token.

        Args:
            token: Session token to revoke.

        Returns:
            bool: True if the session was revoked.
        """
        return self._client.sandboxes.revoke_ssh_session(token)

    def update_metadata(
        self,
        gateway_name: str,
        name: str,
        *,
        description: str | None | object = _UNSET,
        labels: dict[str, str] | None | object = _UNSET,
    ) -> dict[str, Any]:
        """Update labels and/or description for a sandbox.

        Args:
            gateway_name: Name of the gateway.
            name: Sandbox name.
            description: New description (or _UNSET to skip).
            labels: New labels (or _UNSET to skip).

        Returns:
            dict[str, Any]: Updated sandbox record with metadata.

        Raises:
            RuntimeError: If no meta store is configured.
        """
        if not self._meta:
            raise RuntimeError("Metadata store not configured")
        # Verify sandbox exists on gateway
        sb = self._client.sandboxes.get(name)
        self._meta.upsert(gateway_name, name, description=description, labels=labels)
        meta = self._meta.get(gateway_name, name)
        sb["description"] = meta["description"] if meta else None
        sb["labels"] = meta["labels"] if meta else {}
        return sb

    def create(
        self,
        *,
        name: str = "",
        image: str = "",
        gpu: bool = False,
        providers: list[str] | None = None,
        environment: dict[str, str] | None = None,
        presets: list[str] | None = None,
        gateway_name: str | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a sandbox and optionally apply presets.

        This replaces the multi-step wizard polling loop that was in the
        browser JS. The entire workflow happens server-side:
        1. Create sandbox
        2. Wait for ready state
        3. Wait for initial policy
        4. Apply presets sequentially

        Args:
            name: Sandbox name (empty for auto-generated).
            image: Container image to use.
            gpu: Whether to enable GPU support.
            providers: Provider names to attach.
            environment: Environment variables to set.
            presets: Policy presets to apply after creation.
            gateway_name: Gateway name for metadata storage.
            description: Optional sandbox description.
            labels: Optional sandbox labels.

        Returns:
            dict[str, Any]: Created sandbox record with preset status.
        """
        result = self._client.sandboxes.create(
            name=name,
            image=image,
            gpu=gpu,
            providers=providers,
            environment=environment,
        )

        sandbox_name = result.get("name", name)

        # Store metadata if provided
        if self._meta and gateway_name and (description is not None or labels is not None):
            self._meta.upsert(
                gateway_name,
                sandbox_name,
                description=description if description is not None else _UNSET,
                labels=labels if labels is not None else _UNSET,
            )
            meta = self._meta.get(gateway_name, sandbox_name)
            result["description"] = meta["description"] if meta else None
            result["labels"] = meta["labels"] if meta else {}

        if not presets:
            return result

        # Wait for sandbox to become ready
        try:
            self._client.sandboxes.wait_ready(sandbox_name, timeout_seconds=120.0)
        except SandboxError:
            result["preset_error"] = "Sandbox entered error state"
            return result
        except TimeoutError:
            result["preset_error"] = "Sandbox did not become ready in time"
            return result

        # Wait for initial policy to be available
        policy_ready = False
        for _ in range(15):
            try:
                policy_data = self._client.policies.get(sandbox_name)
                if policy_data.get("policy"):
                    policy_ready = True
                    break
            except grpc.RpcError as e:
                logger.debug("Policy not yet available for '%s': %s", sandbox_name, e)
            time.sleep(1)

        if not policy_ready:
            result["preset_warning"] = "Could not read initial policy, presets may fail"

        # Apply presets
        applied = []
        failed = []
        for preset in presets:
            try:
                self._policy.apply_preset(sandbox_name, preset)
                applied.append(preset)
            except Exception as e:
                logger.warning("Failed to apply preset '%s': %s", preset, e, exc_info=True)
                failed.append({"preset": preset, "error": friendly_grpc_error(e)})

        result["presets_applied"] = applied
        if failed:
            result["presets_failed"] = failed

        return result
