"""Pre/post-create hooks attached to sandbox lifecycle.

Stores hooks in the ``sandbox_boot_hooks`` table and runs them in the
right phase around sandbox creation.

Pre-create hooks act as ShoreGuard-side validation gates: they run
via ``subprocess.run`` inside the ShoreGuard process *before* the
gateway sees the ``CreateSandbox`` call, with a whitelisted
environment (sandbox name, image, policy id, plus user-defined
entries). A non-zero exit aborts creation unless the hook is marked
``continue_on_failure``.

Post-create hooks run inside the new sandbox via ``ExecSandbox`` once
creation succeeds and are intended for warm-up tasks where a failure
is typically recoverable from inside the live sandbox rather than
worth rolling creation back.

The execution surface is deliberately on the ShoreGuard side because
the upstream gRPC contract currently has no native hook RPC. Once
one exists, this service can delegate its run loop to the gateway
without the REST surface changing.
"""

from __future__ import annotations

import datetime
import json
import logging
import shlex
import subprocess
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from shoreguard.exceptions import BootHookError, ValidationError, friendly_grpc_error
from shoreguard.models import SandboxBootHook

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm import sessionmaker as SessionMaker

    from shoreguard.services.sandbox import SandboxService

logger = logging.getLogger(__name__)

# Module-level singleton populated during app lifespan startup.
boot_hook_service: BootHookService | None = None

PHASE_PRE = "pre_create"
PHASE_POST = "post_create"
_VALID_PHASES = frozenset({PHASE_PRE, PHASE_POST})

_OUTPUT_LIMIT = 4096
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 600


def _truncate(text: str) -> str:
    """Truncate captured hook output to the persistence limit.

    Args:
        text: Raw stdout+stderr captured from a hook execution.

    Returns:
        str: ``text`` unchanged if short, otherwise truncated with a marker.
    """
    if len(text) <= _OUTPUT_LIMIT:
        return text
    return text[: _OUTPUT_LIMIT - 32] + f"\n... [truncated, {len(text)} bytes]"


class BootHookService:
    """CRUD + execution surface for sandbox boot hooks.

    Args:
        session_factory: SQLAlchemy session factory for database access.
        sandbox_service_provider: Optional callable returning a
            ``SandboxService`` for a given gateway. Used during post-create
            execution to dispatch ``ExecSandbox``. Kept lazy to avoid an
            import cycle with ``shoreguard.services.sandbox``.
    """

    def __init__(  # noqa: D107
        self,
        session_factory: SessionMaker,
        *,
        sandbox_service_provider: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._sandbox_provider = sandbox_service_provider

    # ------------------------------------------------------------------ CRUD

    def list(
        self,
        gateway_name: str,
        sandbox_name: str,
        *,
        phase: str | None = None,
    ) -> list[dict[str, Any]]:
        """List boot hooks for a sandbox, optionally filtered by phase.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox name.
            phase: Optional ``pre_create`` / ``post_create`` filter.

        Returns:
            list[dict[str, Any]]: Hooks ordered by phase, order, id.
        """
        with self._session_factory() as session:
            stmt = select(SandboxBootHook).where(
                SandboxBootHook.gateway_name == gateway_name,
                SandboxBootHook.sandbox_name == sandbox_name,
            )
            if phase is not None:
                self._validate_phase(phase)
                stmt = stmt.where(SandboxBootHook.phase == phase)
            stmt = stmt.order_by(
                SandboxBootHook.phase,
                SandboxBootHook.order,
                SandboxBootHook.id,
            )
            return [self._to_dict(row) for row in session.execute(stmt).scalars()]

    def get(self, hook_id: int) -> dict[str, Any] | None:
        """Fetch a single hook by id.

        Args:
            hook_id: Hook primary key.

        Returns:
            dict[str, Any] | None: Hook data or None if missing.
        """
        with self._session_factory() as session:
            row = session.get(SandboxBootHook, hook_id)
            return self._to_dict(row) if row is not None else None

    def create(
        self,
        *,
        gateway_name: str,
        sandbox_name: str,
        name: str,
        phase: str,
        command: str,
        actor: str,
        workdir: str = "",
        env: dict[str, str] | None = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT,
        order: int | None = None,
        enabled: bool = True,
        continue_on_failure: bool = False,
    ) -> dict[str, Any]:
        """Create a new boot hook.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the hook attaches to.
            name: Human-readable hook name (unique within sandbox+phase).
            phase: ``pre_create`` or ``post_create``.
            command: Shell command (parsed via shlex).
            actor: Identity of the creating user.
            workdir: Optional working directory (post-create only).
            env: Optional extra environment variables.
            timeout_seconds: Wall-clock timeout for the hook execution.
            order: Sort key within phase. Defaults to the next free slot.
            enabled: Whether the hook participates in automatic runs.
            continue_on_failure: If true, post-create failures don't abort
                subsequent hooks.

        Returns:
            dict[str, Any]: The created hook as a dict.
        """
        self._validate_inputs(name=name, phase=phase, command=command, timeout=timeout_seconds)
        with self._session_factory() as session:
            if order is None:
                order = self._next_order(session, gateway_name, sandbox_name, phase)
            now = datetime.datetime.now(datetime.UTC)
            hook = SandboxBootHook(
                gateway_name=gateway_name,
                sandbox_name=sandbox_name,
                name=name,
                phase=phase,
                command=command,
                workdir=workdir,
                env_json=json.dumps(env or {}),
                timeout_seconds=int(timeout_seconds),
                order=int(order),
                enabled=bool(enabled),
                continue_on_failure=bool(continue_on_failure),
                created_by=actor,
                created_at=now,
                updated_at=now,
            )
            session.add(hook)
            session.commit()
            session.refresh(hook)
            return self._to_dict(hook)

    def update(
        self,
        hook_id: int,
        *,
        command: str | None = None,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        order: int | None = None,
        enabled: bool | None = None,
        continue_on_failure: bool | None = None,
    ) -> dict[str, Any] | None:
        """Patch an existing hook.

        Args:
            hook_id: Hook primary key.
            command: New shell command, if provided.
            workdir: New working directory, if provided.
            env: New environment dict, if provided.
            timeout_seconds: New timeout, if provided.
            order: New order index, if provided.
            enabled: New enabled flag, if provided.
            continue_on_failure: New continue-on-failure flag, if provided.

        Returns:
            dict[str, Any] | None: Updated hook or None if not found.

        Raises:
            ValidationError: If new field values fail validation.
        """
        with self._session_factory() as session:
            hook = session.get(SandboxBootHook, hook_id)
            if hook is None:
                return None
            if command is not None:
                if not command.strip():
                    raise ValidationError("Hook command must not be empty")
                hook.command = command
            if workdir is not None:
                hook.workdir = workdir
            if env is not None:
                hook.env_json = json.dumps(env)
            if timeout_seconds is not None:
                self._validate_timeout(timeout_seconds)
                hook.timeout_seconds = int(timeout_seconds)
            if order is not None:
                hook.order = int(order)
            if enabled is not None:
                hook.enabled = bool(enabled)
            if continue_on_failure is not None:
                hook.continue_on_failure = bool(continue_on_failure)
            hook.updated_at = datetime.datetime.now(datetime.UTC)
            session.commit()
            session.refresh(hook)
            return self._to_dict(hook)

    def delete(self, hook_id: int) -> bool:
        """Delete a hook.

        Args:
            hook_id: Hook primary key.

        Returns:
            bool: True if a row was removed.
        """
        with self._session_factory() as session:
            result = session.execute(delete(SandboxBootHook).where(SandboxBootHook.id == hook_id))
            session.commit()
            return result.rowcount > 0  # type: ignore[union-attr]

    def delete_for_sandbox(self, gateway_name: str, sandbox_name: str) -> int:
        """Delete all hooks for a sandbox (called when sandbox is deleted).

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox name.

        Returns:
            int: Number of hooks removed.
        """
        with self._session_factory() as session:
            result = session.execute(
                delete(SandboxBootHook).where(
                    SandboxBootHook.gateway_name == gateway_name,
                    SandboxBootHook.sandbox_name == sandbox_name,
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def reorder(
        self,
        gateway_name: str,
        sandbox_name: str,
        phase: str,
        hook_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Reorder hooks within a phase.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox name.
            phase: ``pre_create`` or ``post_create``.
            hook_ids: New ordering — first id becomes order=0.

        Returns:
            list[dict[str, Any]]: Updated hook list in new order.

        Raises:
            ValidationError: If the id set does not match the stored hooks.
        """
        self._validate_phase(phase)
        with self._session_factory() as session:
            stmt = select(SandboxBootHook).where(
                SandboxBootHook.gateway_name == gateway_name,
                SandboxBootHook.sandbox_name == sandbox_name,
                SandboxBootHook.phase == phase,
            )
            existing = list(session.execute(stmt).scalars())
            existing_ids = {row.id for row in existing}
            if set(hook_ids) != existing_ids:
                raise ValidationError(
                    "reorder hook_ids must match the stored hook set for this phase",
                )
            id_to_row = {row.id: row for row in existing}
            now = datetime.datetime.now(datetime.UTC)
            for index, hook_id in enumerate(hook_ids):
                row = id_to_row[hook_id]
                row.order = index
                row.updated_at = now
            session.commit()
            return [self._to_dict(id_to_row[hook_id]) for hook_id in hook_ids]

    # -------------------------------------------------------------- Execution

    def run_pre_create(
        self,
        gateway_name: str,
        sandbox_name: str,
        spec: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run all enabled pre-create hooks for a sandbox.

        Pre-create hooks execute as ShoreGuard-local subprocesses with a
        whitelisted environment. The first failure raises ``BootHookError``;
        the caller is expected to abort ``CreateSandbox``.

        Args:
            gateway_name: Gateway the sandbox will register on.
            sandbox_name: Planned sandbox name.
            spec: Resolved sandbox spec (image, policy_id, providers, ...).

        Returns:
            list[dict[str, Any]]: One ``HookResult`` dict per executed hook.

        Raises:
            BootHookError: If any hook fails.
        """
        results: list[dict[str, Any]] = []
        for hook in self._enabled_for(gateway_name, sandbox_name, PHASE_PRE):
            result = self._run_local(hook, spec)
            results.append(result)
            if result["status"] == "failure":
                raise BootHookError(
                    f"pre_create hook '{hook['name']}' failed: {result['summary']}",
                    hook_name=hook["name"],
                    phase=PHASE_PRE,
                    output=result.get("output", ""),
                )
        return results

    def run_post_create(
        self,
        gateway_name: str,
        sandbox_name: str,
    ) -> list[dict[str, Any]]:
        """Run all enabled post-create hooks for a sandbox.

        Post-create hooks execute inside the sandbox via ``SandboxService.exec()``.
        Failure handling is per-hook: if ``continue_on_failure`` is false,
        the first failure stops subsequent hooks (the sandbox is **not**
        rolled back).

        Args:
            gateway_name: Gateway the sandbox runs on.
            sandbox_name: Sandbox name.

        Returns:
            list[dict[str, Any]]: One ``HookResult`` per attempted hook.
        """
        results: list[dict[str, Any]] = []
        sandbox_service: SandboxService | None = None
        if self._sandbox_provider is not None:
            try:
                sandbox_service = self._sandbox_provider(gateway_name)
            except Exception:
                logger.exception(
                    "boot_hooks: failed to obtain SandboxService for gateway %s",
                    gateway_name,
                )
                sandbox_service = None
        if sandbox_service is None:
            return results

        for hook in self._enabled_for(gateway_name, sandbox_name, PHASE_POST):
            result = self._run_in_sandbox(hook, sandbox_service)
            results.append(result)
            if result["status"] == "failure" and not hook["continue_on_failure"]:
                logger.warning(
                    "boot_hooks: post_create hook '%s' failed for %s/%s, halting",
                    hook["name"],
                    gateway_name,
                    sandbox_name,
                )
                break
        return results

    def run_one(
        self,
        hook_id: int,
        *,
        spec: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Manually trigger a single hook.

        Used by the ``POST .../hooks/{id}/run`` endpoint. Pre-create hooks
        run with the supplied spec (or an empty one); post-create hooks run
        in the live sandbox.

        Args:
            hook_id: Hook primary key.
            spec: Optional spec dict for pre-create hooks.

        Returns:
            dict[str, Any] | None: Hook execution result or None if missing.
        """
        hook = self.get(hook_id)
        if hook is None:
            return None
        if hook["phase"] == PHASE_PRE:
            return self._run_local(hook, spec or {})
        sandbox_service: SandboxService | None = None
        if self._sandbox_provider is not None:
            try:
                sandbox_service = self._sandbox_provider(hook["gateway_name"])
            except Exception:
                logger.exception(
                    "boot_hooks: manual run failed to resolve SandboxService",
                )
                sandbox_service = None
        if sandbox_service is None:
            failure = self._failure_dict(
                hook,
                "no SandboxService available",
                "",
            )
            self._persist_run(hook["id"], failure)
            return failure
        return self._run_in_sandbox(hook, sandbox_service)

    # ----------------------------------------------------------------- Internals

    def _enabled_for(
        self,
        gateway_name: str,
        sandbox_name: str,
        phase: str,
    ) -> list[dict[str, Any]]:
        """Return enabled hooks for a phase, ordered for execution.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox name.
            phase: ``pre_create`` or ``post_create``.

        Returns:
            list[dict[str, Any]]: Enabled hooks for that phase, in order.
        """
        return [row for row in self.list(gateway_name, sandbox_name, phase=phase) if row["enabled"]]

    def _run_local(
        self,
        hook: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a pre-create hook as a local subprocess.

        Args:
            hook: Hook record (dict form from ``_to_dict``).
            spec: Resolved sandbox spec (image, policy_id, ...).

        Returns:
            dict[str, Any]: ``HookResult`` capturing status + output.
        """
        try:
            argv = shlex.split(hook["command"])
        except ValueError as exc:
            failure = self._failure_dict(
                hook,
                f"invalid command syntax: {exc}",
                "",
            )
            self._persist_run(hook["id"], failure)
            return failure
        if not argv:
            failure = self._failure_dict(hook, "command is empty", "")
            self._persist_run(hook["id"], failure)
            return failure

        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "SG_SANDBOX_NAME": str(spec.get("name", "")),
            "SG_SANDBOX_IMAGE": str(spec.get("image", "")),
            "SG_SANDBOX_POLICY_ID": str(spec.get("policy_id", "")),
            "SG_GATEWAY_NAME": str(hook["gateway_name"]),
            "SG_HOOK_NAME": str(hook["name"]),
        }
        env.update(hook.get("env") or {})

        try:
            completed = subprocess.run(  # noqa: S603 - whitelisted env, fixed argv
                argv,
                capture_output=True,
                text=True,
                timeout=max(1, int(hook["timeout_seconds"])),
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:

            def _to_str(value: object) -> str:
                if isinstance(value, str):
                    return value
                if isinstance(value, (bytes, bytearray)):
                    return bytes(value).decode("utf-8", "replace")
                return ""

            output = _to_str(exc.stdout) + _to_str(exc.stderr)
            failure = self._failure_dict(
                hook,
                f"timeout after {hook['timeout_seconds']}s",
                output,
            )
            self._persist_run(hook["id"], failure)
            return failure
        except FileNotFoundError as exc:
            failure = self._failure_dict(hook, f"command not found: {exc}", "")
            self._persist_run(hook["id"], failure)
            return failure
        except OSError as exc:
            failure = self._failure_dict(hook, f"OS error: {exc}", "")
            self._persist_run(hook["id"], failure)
            return failure

        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode == 0:
            success = self._success_dict(hook, "ok", output)
            self._persist_run(hook["id"], success)
            return success
        failure = self._failure_dict(
            hook,
            f"exit {completed.returncode}",
            output,
        )
        self._persist_run(hook["id"], failure)
        return failure

    def _run_in_sandbox(
        self,
        hook: dict[str, Any],
        sandbox_service: SandboxService,
    ) -> dict[str, Any]:
        """Execute a post-create hook inside the sandbox via ExecSandbox.

        Args:
            hook: Hook record (dict form from ``_to_dict``).
            sandbox_service: Per-gateway ``SandboxService`` used for exec.

        Returns:
            dict[str, Any]: ``HookResult`` capturing status + output.
        """
        try:
            exec_result = sandbox_service.exec(
                hook["sandbox_name"],
                hook["command"],
                workdir=hook.get("workdir") or "",
                env=hook.get("env") or None,
                timeout_seconds=int(hook["timeout_seconds"]),
            )
        except ValidationError as exc:
            failure = self._failure_dict(hook, str(exc), "")
            self._persist_run(hook["id"], failure)
            return failure
        except Exception as exc:  # noqa: BLE001 - normalised below
            failure = self._failure_dict(hook, friendly_grpc_error(exc), "")
            self._persist_run(hook["id"], failure)
            return failure

        stdout = str(exec_result.get("stdout", "") or "")
        stderr = str(exec_result.get("stderr", "") or "")
        exit_code = int(exec_result.get("exit_code", 0) or 0)
        output = stdout + (("\n" + stderr) if stderr else "")
        if exit_code == 0:
            success = self._success_dict(hook, "ok", output)
            self._persist_run(hook["id"], success)
            return success
        failure = self._failure_dict(hook, f"exit {exit_code}", output)
        self._persist_run(hook["id"], failure)
        return failure

    def _persist_run(self, hook_id: int, result: dict[str, Any]) -> None:
        """Write last_run_at/status/output back to the hook row.

        Args:
            hook_id: Hook primary key.
            result: ``HookResult`` dict to persist (status + output).
        """
        with self._session_factory() as session:
            hook = session.get(SandboxBootHook, hook_id)
            if hook is None:
                return
            hook.last_run_at = datetime.datetime.now(datetime.UTC)
            hook.last_status = result["status"]
            hook.last_output = _truncate(result.get("output", "") or "")
            session.commit()

    def _success_dict(
        self,
        hook: dict[str, Any],
        summary: str,
        output: str,
    ) -> dict[str, Any]:
        """Build a success ``HookResult`` dict.

        Args:
            hook: Hook record the result belongs to.
            summary: One-line status description.
            output: Captured stdout+stderr (truncated by caller is fine).

        Returns:
            dict[str, Any]: ``HookResult`` payload with ``status="success"``.
        """
        return {
            "hook_id": hook["id"],
            "name": hook["name"],
            "phase": hook["phase"],
            "status": "success",
            "summary": summary,
            "output": _truncate(output),
        }

    def _failure_dict(
        self,
        hook: dict[str, Any],
        summary: str,
        output: str,
    ) -> dict[str, Any]:
        """Build a failure ``HookResult`` dict.

        Args:
            hook: Hook record the result belongs to.
            summary: One-line failure description.
            output: Captured stdout+stderr.

        Returns:
            dict[str, Any]: ``HookResult`` payload with ``status="failure"``.
        """
        return {
            "hook_id": hook["id"],
            "name": hook["name"],
            "phase": hook["phase"],
            "status": "failure",
            "summary": summary,
            "output": _truncate(output),
        }

    @staticmethod
    def _next_order(
        session: Session,
        gateway_name: str,
        sandbox_name: str,
        phase: str,
    ) -> int:
        """Return the next free order index for a phase.

        Args:
            session: Active SQLAlchemy session.
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox name.
            phase: ``pre_create`` or ``post_create``.

        Returns:
            int: One above the current max order, or 0 if empty.
        """
        rows = session.execute(
            select(SandboxBootHook.order).where(
                SandboxBootHook.gateway_name == gateway_name,
                SandboxBootHook.sandbox_name == sandbox_name,
                SandboxBootHook.phase == phase,
            )
        ).scalars()
        existing = list(rows)
        return (max(existing) + 1) if existing else 0

    @staticmethod
    def _to_dict(row: SandboxBootHook) -> dict[str, Any]:
        """Convert a SandboxBootHook ORM row to a dict.

        Args:
            row: ORM row to serialise.

        Returns:
            dict[str, Any]: Plain-dict representation of the hook.
        """
        try:
            env = json.loads(row.env_json) if row.env_json else {}
        except json.JSONDecodeError:
            env = {}
        return {
            "id": row.id,
            "gateway_name": row.gateway_name,
            "sandbox_name": row.sandbox_name,
            "name": row.name,
            "phase": row.phase,
            "command": row.command,
            "workdir": row.workdir,
            "env": env,
            "timeout_seconds": row.timeout_seconds,
            "order": row.order,
            "enabled": row.enabled,
            "continue_on_failure": row.continue_on_failure,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
            "last_status": row.last_status,
            "last_output": row.last_output,
        }

    @classmethod
    def _validate_inputs(
        cls,
        *,
        name: str,
        phase: str,
        command: str,
        timeout: int,
    ) -> None:
        """Validate hook create inputs.

        Args:
            name: Hook name.
            phase: ``pre_create`` or ``post_create``.
            command: Shell command string.
            timeout: Wall-clock timeout in seconds.

        Raises:
            ValidationError: If any input fails validation.
        """
        if not name or not name.strip():
            raise ValidationError("Hook name must not be empty")
        if len(name) > 128:
            raise ValidationError("Hook name must be 128 characters or fewer")
        cls._validate_phase(phase)
        if not command or not command.strip():
            raise ValidationError("Hook command must not be empty")
        cls._validate_timeout(timeout)

    @staticmethod
    def _validate_phase(phase: str) -> None:
        """Validate the phase string.

        Args:
            phase: Phase identifier to validate.

        Raises:
            ValidationError: If ``phase`` is not a recognised value.
        """
        if phase not in _VALID_PHASES:
            raise ValidationError(
                f"phase must be one of {sorted(_VALID_PHASES)}",
            )

    @staticmethod
    def _validate_timeout(timeout: int) -> None:
        """Validate timeout bounds.

        Args:
            timeout: Wall-clock timeout in seconds.

        Raises:
            ValidationError: If timeout is non-positive or above the cap.
        """
        if not isinstance(timeout, int) or timeout < 1:
            raise ValidationError("timeout_seconds must be a positive integer")
        if timeout > _MAX_TIMEOUT:
            raise ValidationError(f"timeout_seconds must be <= {_MAX_TIMEOUT}")
