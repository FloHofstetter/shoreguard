"""Sandbox lifecycle operations: create, list, delete, exec, metadata.

Wraps the ``ShoreGuardClient.sandboxes`` gRPC surface with
behavior the raw client does not provide: merging sandbox
metadata from the local ``sandbox_meta_store`` into list + detail
responses, running pre-create validation hooks before
``CreateSandbox`` reaches the gateway, and running post-create
warm-up hooks inside the new sandbox once it is up.

The create flow is intentionally centralised here so both the
Web UI and the TUI can share the same preset-application +
metadata + boot-hook sequence — earlier versions of ShoreGuard
duplicated most of it in browser JS.
"""

from __future__ import annotations

import logging
import shlex
import time
from typing import TYPE_CHECKING, Any

import grpc

from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import (
    SandboxError,
    ValidationError,
    friendly_grpc_error,
)
from shoreguard.services.policy import PolicyService
from shoreguard.services.sandbox_meta import _UNSET, SandboxMetaStore

if TYPE_CHECKING:
    from shoreguard.services.boot_hooks import BootHookService

logger = logging.getLogger(__name__)


class SandboxService:
    """Gateway-scoped sandbox lifecycle operations.

    One instance is constructed per ``(gateway, operation)``
    invocation so the embedded gRPC client is already bound to
    the right endpoint. Methods are synchronous because the gRPC
    client is synchronous; async callers wrap them via
    ``asyncio.to_thread``.

    Metadata merging, preset application, and pre/post-create
    hook dispatch happen inside this service rather than in the
    route layer so the same flow is available to the TUI and any
    future programmatic client without duplicated code.

    Args:
        client: OpenShell gRPC client bound to the target gateway.
        meta_store: Optional store for sandbox labels + description
            kept locally in ShoreGuard (OpenShell does not model them).
        boot_hooks: Optional hook service invoked around
            ``create()``; when ``None``, hook support is disabled
            for this instance even if hooks exist in the database.
        gateway_name: Gateway name this service is bound to, used
            for hook lookups when ``create()`` is called without
            an explicit ``gateway_name`` argument.
    """

    def __init__(  # noqa: D107
        self,
        client: ShoreGuardClient,
        meta_store: SandboxMetaStore | None = None,
        *,
        boot_hooks: BootHookService | None = None,
        gateway_name: str | None = None,
    ) -> None:
        self._client = client
        self._policy = PolicyService(client)
        self._meta = meta_store
        self._boot_hooks = boot_hooks
        self._gateway_name = gateway_name

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
        if deleted and self._boot_hooks and gateway_name:
            self._boot_hooks.delete_for_sandbox(gateway_name, name)
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

    def get_config(self, name: str) -> dict[str, Any]:
        """Get the stored sandbox configuration from the gateway.

        Resolves name to sandbox id, then calls ``GetSandboxConfig``. The
        returned dict is the gateway-side spec (policy, providers,
        template, etc.) as the gateway currently has it recorded — the
        source of truth for "what is this sandbox configured with" in the
        pinning / GitOps workflow.

        Args:
            name: Sandbox name.

        Returns:
            dict[str, Any]: Sandbox configuration as returned by the
                gateway.
        """
        sandbox = self._client.sandboxes.get(name)
        return self._client.sandboxes.get_config(sandbox["id"])

    def get_provider_environment(self, name: str) -> dict[str, str]:
        """Get the provider environment variables injected into a sandbox.

        Resolves name to sandbox id, then calls
        ``GetSandboxProviderEnvironment``. Returns the env map the
        gateway will hand to the sandbox's runtime when it launches.
        Values may be secret — callers that surface this over REST
        should redact against the shared secret-key pattern.

        Args:
            name: Sandbox name.

        Returns:
            dict[str, str]: Environment key-value pairs.
        """
        sandbox = self._client.sandboxes.get(name)
        return self._client.sandboxes.get_provider_environment(sandbox["id"])

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
        skip_hooks: bool = False,
        log_level: str = "",
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
            skip_hooks: If true, bypass pre/post boot hook execution
                (admin override for cases where a broken hook would
                block recreation).
            log_level: Supervisor log verbosity (empty = gateway default).

        Returns:
            dict[str, Any]: Created sandbox record with preset status
            and (when hooks ran) ``boot_hooks`` result lists. Pre-create
            hook failures bubble up as ``BootHookError`` from the boot
            hook service.
        """
        effective_gateway = gateway_name or self._gateway_name
        run_hooks = (
            not skip_hooks and self._boot_hooks is not None and effective_gateway is not None
        )

        pre_results: list[dict[str, Any]] = []
        if run_hooks:
            pre_spec: dict[str, Any] = {
                "name": name,
                "image": image,
                "policy_id": "",
                "providers": providers or [],
                "gpu": gpu,
            }
            pre_results = self._boot_hooks.run_pre_create(  # type: ignore[union-attr]
                effective_gateway,  # type: ignore[arg-type]
                name,
                pre_spec,
            )

        result = self._client.sandboxes.create(
            name=name,
            image=image,
            gpu=gpu,
            providers=providers,
            environment=environment,
            log_level=log_level,
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
            if run_hooks:
                result["boot_hooks"] = {
                    "pre_create": pre_results,
                    "post_create": self._boot_hooks.run_post_create(  # type: ignore[union-attr]
                        effective_gateway,  # type: ignore[arg-type]
                        sandbox_name,
                    ),
                }
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

        if run_hooks:
            result["boot_hooks"] = {
                "pre_create": pre_results,
                "post_create": self._boot_hooks.run_post_create(  # type: ignore[union-attr]
                    effective_gateway,  # type: ignore[arg-type]
                    sandbox_name,
                ),
            }
        elif pre_results:
            result["boot_hooks"] = {"pre_create": pre_results, "post_create": []}

        return result
