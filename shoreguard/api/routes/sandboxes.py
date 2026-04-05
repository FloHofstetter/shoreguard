"""REST endpoints for sandbox CRUD and execution."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import grpc
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import friendly_grpc_error
from shoreguard.services.audit import audit_log
from shoreguard.services.operations import operation_store
from shoreguard.services.sandbox import SandboxService
from shoreguard.services.sandbox_meta import sandbox_meta_store
from shoreguard.services.webhooks import fire_webhook

logger = logging.getLogger(__name__)

_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

_MAX_DESCRIPTION_LEN = 1000
_MAX_LABELS = 20
_MAX_LABEL_VALUE_LEN = 253
_LABEL_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")

router = APIRouter()

_background_tasks: set[asyncio.Task] = set()


def _get_sandbox_service(client: ShoreGuardClient = Depends(get_client)) -> SandboxService:
    """Build a SandboxService from the injected client.

    Args:
        client: gRPC client for the active gateway.

    Returns:
        SandboxService: Service instance bound to the client.
    """
    return SandboxService(client, meta_store=sandbox_meta_store)


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
    """

    name: str = ""
    image: str = ""
    providers: list[str] = []
    gpu: bool = False
    environment: dict[str, str] = {}
    policy: dict | None = None
    presets: list[str] = []
    description: str | None = None
    labels: dict[str, str] | None = None


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
    """

    command: str | list[str]
    workdir: str = ""
    env: dict[str, str] = {}
    timeout_seconds: int = 0


class RevokeSshRequest(BaseModel):
    """Body for revoking an SSH session.

    Attributes:
        token: SSH session token to revoke.
    """

    token: str


def _validate_description(description: str | None) -> None:
    """Validate a sandbox description.

    Args:
        description: Description string to validate.

    Raises:
        HTTPException: If the description exceeds the maximum length.
    """
    if description is not None and len(description) > _MAX_DESCRIPTION_LEN:
        raise HTTPException(400, f"description exceeds maximum length of {_MAX_DESCRIPTION_LEN}")


def _validate_labels(labels: dict[str, str] | None) -> None:
    """Validate sandbox labels.

    Args:
        labels: Label dict to validate.

    Raises:
        HTTPException: If any label key or value is invalid, or count exceeds limit.
    """
    if labels is None:
        return
    if len(labels) > _MAX_LABELS:
        raise HTTPException(400, f"Too many labels (max {_MAX_LABELS})")
    for key, value in labels.items():
        if not _LABEL_KEY_RE.match(key):
            raise HTTPException(
                400,
                f"Invalid label key '{key}': must match [a-zA-Z0-9][a-zA-Z0-9._-]* (max 63 chars)",
            )
        if len(value) > _MAX_LABEL_VALUE_LEN:
            raise HTTPException(
                400,
                f"Label value for '{key}' exceeds maximum length of {_MAX_LABEL_VALUE_LEN}",
            )


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


@router.get("")
async def list_sandboxes(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    label: list[str] | None = Query(None),
    svc: SandboxService = Depends(_get_sandbox_service),
) -> list[dict[str, Any]]:
    """List all sandboxes with pagination and optional label filtering.

    Args:
        request: Incoming HTTP request.
        limit: Maximum number of results to return.
        offset: Number of results to skip.
        label: Optional label filters in ``key:value`` format.
        svc: Injected sandbox service.

    Returns:
        list[dict[str, Any]]: Sandbox records.
    """
    labels_filter = _parse_label_filters(label)
    gw = get_gateway_name(request)
    return await asyncio.to_thread(
        svc.list, limit=limit, offset=offset, gateway_name=gw, labels_filter=labels_filter
    )


@router.post("", status_code=202, dependencies=[Depends(require_role("operator"))])
async def create_sandbox(
    body: CreateSandboxRequest,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Create a new sandbox. Returns 202 with an operation ID for polling.

    Args:
        body: Sandbox creation payload.
        request: Incoming HTTP request.
        svc: Injected sandbox service.
        client: gRPC client for the active gateway.

    Returns:
        dict[str, Any]: Operation tracking object with id and status.

    Raises:
        HTTPException: If sandbox name is invalid or creation is already in progress.
    """
    if body.name and not _VALID_NAME_RE.match(body.name):
        raise HTTPException(400, "Invalid sandbox name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")
    _validate_description(body.description)
    _validate_labels(body.labels)
    sandbox_name = body.name or "unnamed"
    actor = get_actor(request)
    op = operation_store.create_if_not_running("sandbox", sandbox_name)
    if op is None:
        raise HTTPException(409, f"Sandbox '{sandbox_name}' creation already in progress")

    _audit_actor = get_actor(request)
    _audit_role = getattr(request.state, "role", "unknown")
    _audit_ip = request.client.host if request.client else None
    _audit_gw = get_gateway_name(request)

    async def _run() -> None:
        """Execute sandbox creation in the background."""
        logger.info("Starting sandbox creation: '%s' (op=%s, actor=%s)", sandbox_name, op.id, actor)
        try:
            result = await asyncio.to_thread(
                svc.create,
                name=body.name,
                image=body.image,
                gpu=body.gpu,
                providers=body.providers or None,
                environment=body.environment or None,
                presets=body.presets or None,
                gateway_name=_audit_gw,
                description=body.description,
                labels=body.labels,
            )
            sb_name = result.get("name", body.name)
            # Always wait for sandbox to become ready before completing
            if sb_name:
                try:
                    await asyncio.to_thread(
                        client.sandboxes.wait_ready,
                        sb_name,
                        timeout_seconds=180.0,
                    )
                    result = await asyncio.to_thread(svc.get, sb_name, gateway_name=_audit_gw)
                except TimeoutError:
                    result["warning"] = "Sandbox created but did not become ready within 180s"
            logger.info(
                "Sandbox creation completed: '%s' (op=%s, actor=%s)",
                sandbox_name,
                op.id,
                actor,
            )
            operation_store.complete(op.id, result)
            from shoreguard.services.audit import audit_service

            if audit_service:
                await asyncio.to_thread(
                    audit_service.log,
                    actor=_audit_actor,
                    actor_role=_audit_role,
                    action="sandbox.create",
                    resource_type="sandbox",
                    resource_id=sandbox_name,
                    gateway=_audit_gw,
                    client_ip=_audit_ip,
                )
            await fire_webhook(
                "sandbox.created",
                {
                    "sandbox": sandbox_name,
                    "actor": _audit_actor,
                    "gateway": _audit_gw,
                    "image": body.image or "",
                    "gpu": body.gpu,
                    "providers": body.providers or [],
                },
            )
        except asyncio.CancelledError:
            logger.warning("Sandbox creation cancelled for '%s'", sandbox_name)
            operation_store.fail(op.id, "Operation was cancelled")
        except (grpc.RpcError, OSError, TimeoutError, RuntimeError) as e:
            logger.error("Sandbox creation failed for '%s': %s", sandbox_name, e, exc_info=True)
            msg = (
                friendly_grpc_error(e)
                if isinstance(e, grpc.RpcError)
                else "Sandbox creation failed unexpectedly"
            )
            try:
                operation_store.fail(op.id, msg)
            except Exception:
                logger.exception("Failed to record operation failure for %s", op.id)
        except Exception:
            logger.exception("Sandbox creation failed unexpectedly for '%s'", sandbox_name)
            try:
                operation_store.fail(op.id, "Unexpected internal error")
            except Exception:
                logger.exception("Failed to record operation failure for %s", op.id)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"operation_id": op.id, "status": "running", "resource_type": "sandbox"}


@router.get("/{name}")
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


@router.patch("/{name}", dependencies=[Depends(require_role("operator"))])
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
    _validate_description(body.description)
    _validate_labels(body.labels)
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


@router.delete("/{name}", dependencies=[Depends(require_role("operator"))])
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
) -> dict[str, Any]:
    """Execute a command inside a running sandbox.

    Accepts command as a string (parsed with shlex) or a list of args.

    Args:
        name: Sandbox name.
        body: Execution request payload.
        request: Incoming HTTP request.
        svc: Injected sandbox service.

    Returns:
        dict[str, Any]: Execution result with stdout, stderr, and exit code.
    """
    result = await asyncio.to_thread(
        svc.exec,
        name,
        body.command,
        workdir=body.workdir,
        env=body.env or None,
        timeout_seconds=body.timeout_seconds,
    )
    logger.info("Command executed in sandbox (sandbox=%s, actor=%s)", name, get_actor(request))
    await audit_log(request, "sandbox.exec", "sandbox", name, gateway=get_gateway_name(request))
    return result


@router.post("/{name}/ssh", status_code=201, dependencies=[Depends(require_role("operator"))])
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


@router.delete("/{name}/ssh", dependencies=[Depends(require_role("operator"))])
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


@router.get("/{name}/logs")
async def get_sandbox_logs(
    name: str,
    lines: int = Query(200, ge=1, le=10000),
    since_ms: int = 0,
    min_level: str = "",
    sources: str = "",
    svc: SandboxService = Depends(_get_sandbox_service),
) -> list[dict[str, Any]]:
    """Fetch recent log entries from a sandbox.

    Args:
        name: Sandbox name.
        lines: Maximum number of log lines to return.
        since_ms: Only return logs newer than this Unix timestamp in ms.
        min_level: Minimum log level filter.
        sources: Comma-separated list of log sources to include.
        svc: Injected sandbox service.

    Returns:
        list[dict[str, Any]]: Log entry records.
    """
    source_list = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    return await asyncio.to_thread(
        svc.get_logs,
        name,
        lines=lines,
        since_ms=since_ms,
        sources=source_list,
        min_level=min_level,
    )
