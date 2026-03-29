"""REST endpoints for policy management."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_client
from shoreguard.client import ShoreGuardClient
from shoreguard.presets import get_preset as _get_preset
from shoreguard.presets import list_presets as _list_presets
from shoreguard.services.policy import PolicyService

router = APIRouter()


def _get_policy_service(client: ShoreGuardClient = Depends(get_client)) -> PolicyService:
    return PolicyService(client)


class NetworkRuleRequest(BaseModel):
    """Body for adding/updating a network rule."""

    key: str
    rule: dict[str, Any]


class FilesystemPathRequest(BaseModel):
    """Body for adding a filesystem path."""

    path: str
    access: Literal["ro", "rw"]


class ProcessPolicyRequest(BaseModel):
    """Body for updating process/landlock settings."""

    run_as_user: str | None = None
    run_as_group: str | None = None
    landlock_compatibility: str | None = None


@router.get("/sandboxes/{name}/policy")
async def get_policy(
    name: str,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Get the active policy for a sandbox."""
    return await asyncio.to_thread(svc.get, name)


@router.put("/sandboxes/{name}/policy", dependencies=[Depends(require_role("operator"))])
async def update_policy(
    name: str,
    body: dict[str, Any],
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Push a new policy version to a sandbox."""
    return await asyncio.to_thread(svc.update, name, body)


@router.get("/sandboxes/{name}/policy/revisions")
async def list_policy_revisions(
    name: str,
    limit: int = 20,
    offset: int = 0,
    svc: PolicyService = Depends(_get_policy_service),
) -> list[dict[str, Any]]:
    """List policy revision history for a sandbox."""
    return await asyncio.to_thread(svc.list_revisions, name, limit=limit, offset=offset)


# ─── Network Rule CRUD ───────────────────────────────────────────────────────


@router.post(
    "/sandboxes/{name}/policy/network-rules", dependencies=[Depends(require_role("operator"))]
)
async def add_network_rule(
    name: str,
    body: NetworkRuleRequest,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Add or update a single network rule."""
    return await asyncio.to_thread(svc.add_network_rule, name, body.key, body.rule)


@router.delete(
    "/sandboxes/{name}/policy/network-rules/{key}", dependencies=[Depends(require_role("operator"))]
)
async def delete_network_rule(
    name: str,
    key: str,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Delete a single network rule."""
    return await asyncio.to_thread(svc.delete_network_rule, name, key)


# ─── Filesystem Path CRUD ─────────────────────────────────────────────


@router.post(
    "/sandboxes/{name}/policy/filesystem", dependencies=[Depends(require_role("operator"))]
)
async def add_filesystem_path(
    name: str,
    body: FilesystemPathRequest,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Add or update a filesystem path."""
    return await asyncio.to_thread(svc.add_filesystem_path, name, body.path, body.access)


@router.delete(
    "/sandboxes/{name}/policy/filesystem", dependencies=[Depends(require_role("operator"))]
)
async def delete_filesystem_path(
    name: str,
    path: str,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Delete a filesystem path (pass path as query param)."""
    return await asyncio.to_thread(svc.delete_filesystem_path, name, path)


# ─── Process/Landlock Update ─────────────────────────────────────────


@router.put("/sandboxes/{name}/policy/process", dependencies=[Depends(require_role("operator"))])
async def update_process_policy(
    name: str,
    body: ProcessPolicyRequest,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Update process and landlock settings."""
    return await asyncio.to_thread(
        svc.update_process_policy,
        name,
        run_as_user=body.run_as_user,
        run_as_group=body.run_as_group,
        landlock_compatibility=body.landlock_compatibility,
    )


# ─── Presets ──────────────────────────────────────────────────────────────────


@router.get("/policies/presets")
async def list_presets() -> list[dict[str, str]]:
    """List available policy presets (local YAML files, no gateway needed)."""
    return _list_presets()


@router.get("/policies/presets/{preset_name}")
async def get_preset(preset_name: str) -> dict[str, Any]:
    """Load a single policy preset by name."""
    result = _get_preset(preset_name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_name}' not found")
    return result


@router.post(
    "/sandboxes/{name}/policy/presets/{preset_name}",
    dependencies=[Depends(require_role("operator"))],
)
async def apply_preset(
    name: str,
    preset_name: str,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Apply a policy preset to a sandbox."""
    return await asyncio.to_thread(svc.apply_preset, name, preset_name)
