"""REST endpoints for sandbox CRUD and execution."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from shoreguard.api.deps import get_client
from shoreguard.client import ShoreGuardClient
from shoreguard.services.sandbox import SandboxService

router = APIRouter()


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
    limit: int = 100,
    offset: int = 0,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> list[dict[str, Any]]:
    """List all sandboxes with pagination."""
    return await asyncio.to_thread(svc.list, limit=limit, offset=offset)


@router.post("", status_code=201)
async def create_sandbox(
    body: CreateSandboxRequest,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Create a new sandbox, optionally applying presets."""
    return await asyncio.to_thread(
        svc.create,
        name=body.name,
        image=body.image,
        gpu=body.gpu,
        providers=body.providers or None,
        environment=body.environment or None,
        presets=body.presets or None,
    )


@router.get("/{name}")
async def get_sandbox(
    name: str,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Get a sandbox by name."""
    return await asyncio.to_thread(svc.get, name)


@router.delete("/{name}")
async def delete_sandbox(
    name: str,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, bool]:
    """Delete a sandbox by name."""
    deleted = await asyncio.to_thread(svc.delete, name)
    return {"deleted": deleted}


@router.post("/{name}/exec")
async def exec_in_sandbox(
    name: str,
    body: ExecRequest,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Execute a command inside a running sandbox.

    Accepts command as a string (parsed with shlex) or a list of args.
    """
    return await asyncio.to_thread(
        svc.exec,
        name,
        body.command,
        workdir=body.workdir,
        env=body.env or None,
        timeout_seconds=body.timeout_seconds,
    )


@router.post("/{name}/ssh", status_code=201)
async def create_ssh_session(
    name: str,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, Any]:
    """Create a temporary SSH session for shell access to a sandbox."""
    return await asyncio.to_thread(svc.create_ssh_session, name)


@router.delete("/{name}/ssh")
async def revoke_ssh_session(
    name: str,
    body: RevokeSshRequest,
    svc: SandboxService = Depends(_get_sandbox_service),
) -> dict[str, bool]:
    """Revoke an active SSH session."""
    revoked = await asyncio.to_thread(svc.revoke_ssh_session, body.token)
    return {"revoked": revoked}


@router.get("/{name}/logs")
async def get_sandbox_logs(
    name: str,
    lines: int = 200,
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
