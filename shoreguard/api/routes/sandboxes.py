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
from shoreguard.api.deps import get_client
from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import friendly_grpc_error
from shoreguard.services.operations import operation_store
from shoreguard.services.sandbox import SandboxService

logger = logging.getLogger(__name__)

_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

router = APIRouter()

_background_tasks: set[asyncio.Task] = set()


def _actor(request: Request) -> str:
    return getattr(request.state, "user_id", "unknown")


def _get_sandbox_service(client: ShoreGuardClient = Depends(get_client)) -> SandboxService:
    return SandboxService(client)


class CreateSandboxRequest(BaseModel):
    """Body for creating a new sandbox."""

    name: str = ""
    image: str = ""
    providers: list[str] = []
    gpu: bool = False
    environment: dict[str, str] = {}
    policy: dict | None = None
    presets: list[str] = []


class ExecRequest(BaseModel):
    """Body for executing a command in a sandbox."""

    command: str | list[str]
    workdir: str = ""
    env: dict[str, str] = {}
    timeout_seconds: int = 0


class RevokeSshRequest(BaseModel):
    """Body for revoking an SSH session."""

    token: str


@router.get("")
async def list_sandboxes(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    svc: SandboxService = Depends(_get_sandbox_service),
) -> list[dict[str, Any]]:
    """List all sandboxes with pagination."""
    return await asyncio.to_thread(svc.list, limit=limit, offset=offset)


@router.post("", status_code=202, dependencies=[Depends(require_role("operator"))])
async def create_sandbox(
    body: CreateSandboxRequest,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
    client: ShoreGuardClient = Depends(get_client),
) -> dict[str, Any]:
    """Create a new sandbox. Returns 202 with an operation ID for polling."""
    if body.name and not _VALID_NAME_RE.match(body.name):
        raise HTTPException(400, "Invalid sandbox name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")
    sandbox_name = body.name or "unnamed"
    actor = _actor(request)
    op = operation_store.create_if_not_running("sandbox", sandbox_name)
    if op is None:
        raise HTTPException(409, f"Sandbox '{sandbox_name}' creation already in progress")

    async def _run() -> None:
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
                    result = await asyncio.to_thread(svc.get, sb_name)
                except TimeoutError:
                    result["warning"] = "Sandbox created but did not become ready within 180s"
            logger.info(
                "Sandbox creation completed: '%s' (op=%s, actor=%s)",
                sandbox_name,
                op.id,
                actor,
            )
            operation_store.complete(op.id, result)
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
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Get a sandbox by name."""
    return await asyncio.to_thread(svc.get, name)


@router.delete("/{name}", dependencies=[Depends(require_role("operator"))])
async def delete_sandbox(
    name: str,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, bool]:
    """Delete a sandbox by name."""
    deleted = await asyncio.to_thread(svc.delete, name)
    logger.info("Sandbox deleted (sandbox=%s, actor=%s)", name, _actor(request))
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
    """
    result = await asyncio.to_thread(
        svc.exec,
        name,
        body.command,
        workdir=body.workdir,
        env=body.env or None,
        timeout_seconds=body.timeout_seconds,
    )
    logger.info("Command executed in sandbox (sandbox=%s, actor=%s)", name, _actor(request))
    return result


@router.post("/{name}/ssh", status_code=201, dependencies=[Depends(require_role("operator"))])
async def create_ssh_session(
    name: str,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Create a temporary SSH session for shell access to a sandbox."""
    result = await asyncio.to_thread(svc.create_ssh_session, name)
    logger.info("SSH session created (sandbox=%s, actor=%s)", name, _actor(request))
    return result


@router.delete("/{name}/ssh", dependencies=[Depends(require_role("operator"))])
async def revoke_ssh_session(
    name: str,
    body: RevokeSshRequest,
    request: Request,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, bool]:
    """Revoke an active SSH session."""
    revoked = await asyncio.to_thread(svc.revoke_ssh_session, body.token)
    logger.info("SSH session revoked (sandbox=%s, actor=%s)", name, _actor(request))
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
    """Fetch recent log entries from a sandbox."""
    source_list = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    return await asyncio.to_thread(
        svc.get_logs,
        name,
        lines=lines,
        since_ms=since_ms,
        sources=source_list,
        min_level=min_level,
    )
