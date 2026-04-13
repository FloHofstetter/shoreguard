"""REST endpoints for sandbox lifecycle and execution.

Covers the full sandbox surface a UI or agent-framework adapter
needs: list with label filtering, create (with optional
``skip_hooks`` admin override), detail, delete, execute a command
(with or without TTY mode), SSH session open/revoke, and tail
logs. Merged metadata — the labels and description kept in
ShoreGuard's own store — is applied at read time so callers see
a single unified record.

Writes forward through
:class:`~shoreguard.services.sandbox.SandboxService`, which owns
boot-hook dispatch, preset application, and the 202/poll
pattern for long-running creates.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.lro import run_lro
from shoreguard.api.schemas import (
    LogEntryResponse,
    PaginatedResponse,
    SandboxDeleteResponse,
    SandboxResponse,
    SshRevokeResponse,
    SshSessionResponse,
)
from shoreguard.api.validation import check_write_rate_limit, validate_description, validate_labels
from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import ValidationError
from shoreguard.services import operations as _ops_mod
from shoreguard.services import sandbox_meta as _sandbox_meta_mod
from shoreguard.services.audit import audit_log
from shoreguard.services.ocsf import parse_log_line as parse_ocsf_log
from shoreguard.services.sandbox import SandboxService
from shoreguard.services.webhooks import fire_webhook

logger = logging.getLogger(__name__)

_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

router = APIRouter()


def _get_sandbox_service(
    client: ShoreGuardClient = Depends(get_client),
    gateway_name: str = Depends(get_gateway_name),
) -> SandboxService:
    """Build a SandboxService from the injected client.

    Args:
        client: gRPC client for the active gateway.
        gateway_name: Name of the active gateway (for boot hook lookups).

    Returns:
        SandboxService: Service instance bound to the client.
    """
    from shoreguard.services import boot_hooks as _boot_hooks_mod

    return SandboxService(
        client,
        meta_store=_sandbox_meta_mod.sandbox_meta_store,
        boot_hooks=_boot_hooks_mod.boot_hook_service,
        gateway_name=gateway_name,
    )


class CreateSandboxRequest(BaseModel):
    """Body for creating a new sandbox.

    Attributes:
        name: Sandbox name (optional, defaults to "unnamed").
        image: Container image to use.
        providers: List of provider names to attach.
        gpu: Whether to enable GPU access.
        environment: Environment variables to set.
        policy: Optional policy to apply.
        presets: Policy presets to apply.
        description: Optional free-text description.
        labels: Optional key-value labels.
        skip_hooks: Admin-only flag to bypass pre/post-create boot hooks.
    """

    name: str = Field(default="", max_length=253)
    image: str = Field(default="", max_length=512)
    providers: list[str] = Field(default_factory=list, max_length=20)
    gpu: bool = False
    environment: dict[str, str] = Field(default_factory=dict)
    policy: dict | None = None
    presets: list[str] = Field(default_factory=list, max_length=20)
    description: str | None = None
    labels: dict[str, str] | None = None
    skip_hooks: bool = Field(default=False)

    @field_validator("environment")
    @classmethod
    def check_env(cls, v: dict[str, str]) -> dict[str, str]:
        """Enforce entry count and key/value length limits.

        Args:
            v: Environment variables to validate.

        Returns:
            dict[str, str]: The validated environment mapping.

        Raises:
            ValueError: If too many entries or key/value exceed length limits.
        """
        if len(v) > 100:
            raise ValueError("too many environment variables (max 100)")
        for k, val in v.items():
            if len(k) > 256 or len(val) > 8192:
                raise ValueError("env key max 256 chars, value max 8192 chars")
        return v


class UpdateSandboxMetadataRequest(BaseModel):
    """Request body for updating sandbox description and/or labels.

    Attributes:
        description: New description (or null to clear).
        labels: New key-value labels (or null to clear).
    """

    description: str | None = None
    labels: dict[str, str] | None = None


class ExecRequest(BaseModel):
    """Body for executing a command in a sandbox.

    Attributes:
        command: Command string or list of arguments to execute.
        workdir: Working directory for the command.
        env: Environment variables for the command.
        timeout_seconds: Execution timeout in seconds (0 = no timeout).
        tty: Allocate a TTY for the command — set true for interactive
            programs that check ``isatty()`` (e.g. python REPL, vim).
            Added in OpenShell v0.0.23.
    """

    command: str | list[str]
    workdir: str = Field(default="", max_length=4096)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=0, ge=0, le=3600)
    tty: bool = False

    @field_validator("env")
    @classmethod
    def check_env(cls, v: dict[str, str]) -> dict[str, str]:
        """Enforce entry count and key/value length limits.

        Args:
            v: Environment variables to validate.

        Returns:
            dict[str, str]: The validated environment mapping.

        Raises:
            ValueError: If too many entries or key/value exceed length limits.
        """
        if len(v) > 100:
            raise ValueError("too many environment variables (max 100)")
        for k, val in v.items():
            if len(k) > 256 or len(val) > 8192:
                raise ValueError("env key max 256 chars, value max 8192 chars")
        return v


class RevokeSshRequest(BaseModel):
    """Body for revoking an SSH session.

    Attributes:
        token: SSH session token to revoke.
    """

    token: str


def _parse_label_filters(label: list[str] | None) -> dict[str, str] | None:
    """Parse label query parameters into a filter dict.

    Args:
        label: List of ``key:value`` strings.

    Returns:
        dict[str, str] | None: Parsed filter or None.

    Raises:
        HTTPException: If a filter string has invalid format.
    """
    if not label:
        return None
    result: dict[str, str] = {}
    for item in label:
        if ":" not in item or item.startswith(":"):
            raise HTTPException(400, f"Invalid label filter '{item}': expected key:value")
        key, value = item.split(":", 1)
        result[key] = value
    return result


@router.get("", response_model=PaginatedResponse)
async def list_sandboxes(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    label: list[str] | None = Query(None),
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """List all sandboxes with pagination and optional label filtering.

    Args:
        request: Incoming HTTP request.
        limit: Maximum number of results to return.
        offset: Number of results to skip.
        label: Optional label filters in ``key:value`` format.
        svc: Injected sandbox service.

    Returns:
        dict[str, Any]: Paginated sandbox records.
    """
    labels_filter = _parse_label_filters(label)
    gw = get_gateway_name(request)
    items = await asyncio.to_thread(
        svc.list, limit=limit, offset=offset, gateway_name=gw, labels_filter=labels_filter
    )
    return {"items": items, "total": None}


@router.post("", dependencies=[Depends(require_role("operator"))])
async def create_sandbox(
    body: CreateSandboxRequest,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
    client: ShoreGuardClient = Depends(get_client),
) -> JSONResponse:
    """Create a new sandbox. Returns 202 with an operation ID for polling.

    Args:
        body: Sandbox creation payload.
        request: Incoming HTTP request.
        svc: Injected sandbox service.
        client: gRPC client for the active gateway.

    Returns:
        JSONResponse: Operation tracking object with id and status.

    Raises:
        HTTPException: If sandbox name is invalid or creation is already in progress.
        AssertionError: If the operation service is not initialized.
    """
    check_write_rate_limit(request)
    if body.name and not _VALID_NAME_RE.match(body.name):
        raise HTTPException(400, "Invalid sandbox name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")
    validate_description(body.description)
    validate_labels(body.labels)
    if body.skip_hooks and getattr(request.state, "role", None) != "admin":
        raise HTTPException(403, "skip_hooks requires admin role")
    sandbox_name = body.name or "unnamed"
    actor = get_actor(request)
    gw = get_gateway_name(request)

    async def work(op):
        logger.info("Starting sandbox creation: '%s' (op=%s, actor=%s)", sandbox_name, op.id, actor)
        result = await asyncio.to_thread(
            svc.create,
            name=body.name,
            image=body.image,
            gpu=body.gpu,
            providers=body.providers or None,
            environment=body.environment or None,
            presets=body.presets or None,
            gateway_name=gw,
            description=body.description,
            labels=body.labels,
            skip_hooks=body.skip_hooks,
        )
        sb_name = result.get("name", body.name)
        assert _ops_mod.operation_service is not None
        await _ops_mod.operation_service.update_progress(op.id, 30, "Waiting for ready state")  # type: ignore[misc]
        if sb_name:
            try:
                from shoreguard.settings import get_settings

                ready_timeout = get_settings().sandbox.ready_timeout
                await asyncio.to_thread(
                    client.sandboxes.wait_ready, sb_name, timeout_seconds=ready_timeout
                )
                result = await asyncio.to_thread(svc.get, sb_name, gateway_name=gw)
            except TimeoutError:
                result["warning"] = "Sandbox created but did not become ready in time"
        logger.info("Sandbox creation completed: '%s' (op=%s)", sandbox_name, op.id)
        await audit_log(request, "sandbox.create", "sandbox", sandbox_name, gateway=gw)
        await fire_webhook(
            "sandbox.created",
            {
                "sandbox": sandbox_name,
                "actor": actor,
                "gateway": gw,
                "image": body.image or "",
                "gpu": body.gpu,
                "providers": body.providers or [],
            },
        )
        return result

    return await run_lro(
        resource_type="sandbox",
        resource_key=sandbox_name,
        work=work,
        unique=True,
        actor=actor,
        gateway_name=gw,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )


@router.get("/{name}", response_model=SandboxResponse)
async def get_sandbox(
    name: str,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Get a sandbox by name.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.
        svc: Injected sandbox service.

    Returns:
        dict[str, Any]: Sandbox record.
    """
    gw = get_gateway_name(request)
    return await asyncio.to_thread(svc.get, name, gateway_name=gw)


@router.patch(
    "/{name}",
    response_model=SandboxResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def update_sandbox_metadata(
    name: str,
    body: UpdateSandboxMetadataRequest,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Update labels and/or description for a sandbox.

    Args:
        name: Sandbox name.
        body: Metadata update payload.
        request: Incoming HTTP request.
        svc: Injected sandbox service.

    Returns:
        dict[str, Any]: Updated sandbox record with metadata.
    """
    validate_description(body.description)
    validate_labels(body.labels)
    gw = get_gateway_name(request)
    from shoreguard.services.sandbox_meta import _UNSET

    result = await asyncio.to_thread(
        svc.update_metadata,
        gw,
        name,
        description=body.description if body.description is not None else _UNSET,
        labels=body.labels if body.labels is not None else _UNSET,
    )
    logger.info("Sandbox metadata updated (sandbox=%s, actor=%s)", name, get_actor(request))
    await audit_log(request, "sandbox.metadata.update", "sandbox", name, gateway=gw)
    return result


@router.delete(
    "/{name}",
    response_model=SandboxDeleteResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def delete_sandbox(
    name: str,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, bool]:
    """Delete a sandbox by name.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.
        svc: Injected sandbox service.

    Returns:
        dict[str, bool]: Deletion status.
    """
    gw = get_gateway_name(request)
    deleted = await asyncio.to_thread(svc.delete, name, gateway_name=gw)
    if deleted:
        actor = get_actor(request)
        logger.info("Sandbox deleted (sandbox=%s, actor=%s)", name, actor)
        await audit_log(request, "sandbox.delete", "sandbox", name, gateway=gw)
        await fire_webhook(
            "sandbox.deleted",
            {"sandbox": name, "actor": actor, "gateway": gw},
        )
    return {"deleted": deleted}


@router.post("/{name}/exec", dependencies=[Depends(require_role("operator"))])
async def exec_in_sandbox(
    name: str,
    body: ExecRequest,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> JSONResponse:
    """Execute a command inside a running sandbox (async LRO).

    Returns 202 with an operation ID. Poll ``GET /operations/{id}``
    for the result.

    Args:
        name: Sandbox name.
        body: Execution request payload.
        request: Incoming HTTP request.
        svc: Injected sandbox service.

    Returns:
        JSONResponse: Operation ID and status for polling.

    Raises:
        ValidationError: If the command string has invalid shell syntax.
    """
    check_write_rate_limit(request)
    if isinstance(body.command, str):
        try:
            shlex.split(body.command)
        except ValueError as e:
            raise ValidationError(f"Invalid command syntax: {e}") from e

    actor = get_actor(request)
    gw = get_gateway_name(request)

    async def work(op):
        result = await asyncio.to_thread(
            svc.exec,
            name,
            body.command,
            workdir=body.workdir,
            env=body.env or None,
            timeout_seconds=body.timeout_seconds,
            tty=body.tty,
        )
        exit_code = result.get("exit_code")
        logger.info("Exec completed (sandbox=%s, actor=%s)", name, actor)
        await audit_log(
            request,
            "sandbox.exec",
            "sandbox",
            name,
            gateway=gw,
            detail={
                "command": body.command[:200],
                "exit_code": exit_code,
                "timeout_seconds": body.timeout_seconds,
                "tty": body.tty,
                "status": "success" if exit_code == 0 else "failed",
            },
        )
        return result

    return await run_lro(
        resource_type="exec",
        resource_key=f"{name}:{body.command[:60]}",
        work=work,
        actor=actor,
        gateway_name=gw,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )


@router.post(
    "/{name}/ssh",
    status_code=201,
    response_model=SshSessionResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def create_ssh_session(
    name: str,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Create a temporary SSH session for shell access to a sandbox.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.
        svc: Injected sandbox service.

    Returns:
        dict[str, Any]: SSH session details including token and connection info.
    """
    result = await asyncio.to_thread(svc.create_ssh_session, name)
    logger.info("SSH session created (sandbox=%s, actor=%s)", name, get_actor(request))
    await audit_log(
        request, "sandbox.ssh.create", "sandbox", name, gateway=get_gateway_name(request)
    )
    return result


@router.delete(
    "/{name}/ssh",
    response_model=SshRevokeResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def revoke_ssh_session(
    name: str,
    body: RevokeSshRequest,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, bool]:
    """Revoke an active SSH session.

    Args:
        name: Sandbox name.
        body: Revocation request with session token.
        request: Incoming HTTP request.
        svc: Injected sandbox service.

    Returns:
        dict[str, bool]: Revocation status.
    """
    revoked = await asyncio.to_thread(svc.revoke_ssh_session, body.token)
    logger.info("SSH session revoked (sandbox=%s, actor=%s)", name, get_actor(request))
    await audit_log(
        request, "sandbox.ssh.revoke", "sandbox", name, gateway=get_gateway_name(request)
    )
    return {"revoked": revoked}


# Numeric severity ranking used for local min_level filtering.
#
# Mirrors the OpenShell gateway's level_matches() helper in
# crates/openshell-server/src/grpc/validation.rs. We replicate the filter in
# ShoreGuard so OCSF events — which carry an unknown "OCSF" level that the
# gateway maps to rank 5 and silently drops for any non-empty min_level — stay
# in the result set.
_LEVEL_RANKS: dict[str, int] = {
    "ERROR": 0,
    "WARN": 1,
    "INFO": 2,
    "DEBUG": 3,
    "TRACE": 4,
}


def _filter_by_min_level(logs: list[dict[str, Any]], min_level: str) -> list[dict[str, Any]]:
    """Drop entries whose level is below *min_level*, keeping OCSF events.

    Args:
        logs: Log entry dicts as returned by ``SandboxService.get_logs``.
        min_level: Minimum severity threshold (case-insensitive). Empty string
            disables filtering.

    Returns:
        list[dict[str, Any]]: Entries whose numeric level rank is ``<=`` the
        threshold rank. OCSF entries (detected via ``level == "OCSF"`` or
        ``target == "ocsf"``) bypass the filter unconditionally.
    """
    if not min_level:
        return logs
    threshold = _LEVEL_RANKS.get(min_level.upper())
    if threshold is None:
        return logs
    kept: list[dict[str, Any]] = []
    for entry in logs:
        level = str(entry.get("level") or "").upper()
        target = str(entry.get("target") or "").lower()
        if level == "OCSF" or target == "ocsf":
            kept.append(entry)
            continue
        rank = _LEVEL_RANKS.get(level, 5)
        if rank <= threshold:
            kept.append(entry)
    return kept


def _split_csv(value: str) -> set[str]:
    """Return an uppercase token set from a comma-separated query string value.

    Args:
        value: Raw query string, e.g. ``"NET,HTTP,finding"``.

    Returns:
        set[str]: Upper-cased, whitespace-stripped, non-empty tokens.
    """
    return {token.strip().upper() for token in value.split(",") if token.strip()}


@router.get("/{name}/logs", response_model=list[LogEntryResponse])
async def get_sandbox_logs(
    name: str,
    lines: int = Query(200, ge=1, le=10000),
    since_ms: int = 0,
    min_level: str = "",
    sources: str = "",
    ocsf_only: bool = False,
    ocsf_class: str = "",
    ocsf_disposition: str = "",
    ocsf_severity: str = "",
    svc: SandboxService = Depends(_get_sandbox_service),
) -> list[dict[str, Any]]:
    """Fetch recent log entries from a sandbox.

    Args:
        name: Sandbox name.
        lines: Maximum number of log lines to return.
        since_ms: Only return logs newer than this Unix timestamp in ms.
        min_level: Minimum severity filter (``ERROR``/``WARN``/``INFO``/
            ``DEBUG``/``TRACE``). Applied locally, OCSF entries are never
            dropped by this filter (gateway-side ``min_level`` would silently
            drop them — see ``_filter_by_min_level``).
        sources: Comma-separated list of log sources to include.
        ocsf_only: When true, drop every entry that is not an OCSF event.
        ocsf_class: Comma-separated OCSF class prefixes to keep (e.g.
            ``NET,HTTP,FINDING``). Non-OCSF entries are dropped when set.
        ocsf_disposition: Comma-separated OCSF dispositions to keep
            (``ALLOWED``/``DENIED``/``BLOCKED``).
        ocsf_severity: Comma-separated OCSF severities to keep
            (``INFO``/``LOW``/``MED``/``HIGH``/``CRIT``/``FATAL``).
        svc: Injected sandbox service.

    Returns:
        list[dict[str, Any]]: Log entry records.
    """
    source_list = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    # Always pull with min_level="" so OCSF events survive the gateway filter.
    logs = await asyncio.to_thread(
        svc.get_logs,
        name,
        lines=lines,
        since_ms=since_ms,
        sources=source_list,
        min_level="",
    )
    for entry in logs:
        ocsf = parse_ocsf_log(entry)
        if ocsf is not None:
            entry["ocsf"] = ocsf

    logs = _filter_by_min_level(logs, min_level)

    if ocsf_only:
        logs = [e for e in logs if "ocsf" in e]

    if ocsf_class:
        wanted = _split_csv(ocsf_class)
        logs = [e for e in logs if "ocsf" in e and (e["ocsf"]["class_prefix"] or "") in wanted]

    if ocsf_disposition:
        wanted = _split_csv(ocsf_disposition)
        logs = [e for e in logs if "ocsf" in e and (e["ocsf"]["disposition"] or "") in wanted]

    if ocsf_severity:
        wanted = _split_csv(ocsf_severity)
        logs = [e for e in logs if "ocsf" in e and (e["ocsf"]["severity"] or "") in wanted]

    return logs
