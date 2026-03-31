"""REST endpoints for policy management."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from shoreguard.api.auth import require_role
from shoreguard.api.deps import _current_gateway, get_actor, get_client
from shoreguard.client import ShoreGuardClient
from shoreguard.presets import get_preset as _get_preset
from shoreguard.presets import list_presets as _list_presets
from shoreguard.services.audit import audit_log
from shoreguard.services.policy import PolicyService

logger = logging.getLogger(__name__)

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


@router.put(
    "/sandboxes/{name}/policy",
    dependencies=[Depends(require_role("operator"))],
)
async def update_policy(
    name: str,
    body: dict[str, Any],
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Push a new policy version to a sandbox."""
    result = await asyncio.to_thread(svc.update, name, body)
    logger.info(
        "Policy updated (sandbox=%s, actor=%s)",
        name,
        get_actor(request),
    )
    await audit_log(request, "policy.update", "policy", name, gateway=_current_gateway.get())
    return result


@router.get("/sandboxes/{name}/policy/revisions")
async def list_policy_revisions(
    name: str,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    svc: PolicyService = Depends(_get_policy_service),
) -> list[dict[str, Any]]:
    """List policy revision history for a sandbox."""
    return await asyncio.to_thread(
        svc.list_revisions,
        name,
        limit=limit,
        offset=offset,
    )


@router.get("/sandboxes/{name}/policy/diff")
async def diff_policy_revisions(
    name: str,
    version_a: int = Query(..., ge=1),
    version_b: int = Query(..., ge=1),
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Compare two policy revisions side-by-side."""
    return await asyncio.to_thread(svc.diff_revisions, name, version_a, version_b)


# ─── Network Rule CRUD ───────────────────────────────────────────────────────


@router.post(
    "/sandboxes/{name}/policy/network-rules",
    dependencies=[Depends(require_role("operator"))],
)
async def add_network_rule(
    name: str,
    body: NetworkRuleRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Add or update a single network rule."""
    result = await asyncio.to_thread(
        svc.add_network_rule,
        name,
        body.key,
        body.rule,
    )
    logger.info(
        "Network rule added (sandbox=%s, actor=%s)",
        name,
        get_actor(request),
    )
    await audit_log(
        request,
        "policy.network_rule.add",
        "policy",
        name,
        gateway=_current_gateway.get(),
        detail={"key": body.key},
    )
    return result


@router.delete(
    "/sandboxes/{name}/policy/network-rules/{key}",
    dependencies=[Depends(require_role("operator"))],
)
async def delete_network_rule(
    name: str,
    key: str,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Delete a single network rule."""
    result = await asyncio.to_thread(svc.delete_network_rule, name, key)
    logger.info(
        "Network rule deleted (sandbox=%s, key=%s, actor=%s)",
        name,
        key,
        get_actor(request),
    )
    await audit_log(
        request,
        "policy.network_rule.delete",
        "policy",
        name,
        gateway=_current_gateway.get(),
        detail={"key": key},
    )
    return result


# ─── Filesystem Path CRUD ─────────────────────────────────────────────


@router.post(
    "/sandboxes/{name}/policy/filesystem",
    dependencies=[Depends(require_role("operator"))],
)
async def add_filesystem_path(
    name: str,
    body: FilesystemPathRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Add or update a filesystem path."""
    result = await asyncio.to_thread(
        svc.add_filesystem_path,
        name,
        body.path,
        body.access,
    )
    logger.info(
        "Filesystem path added (sandbox=%s, actor=%s)",
        name,
        get_actor(request),
    )
    await audit_log(
        request,
        "policy.filesystem.add",
        "policy",
        name,
        gateway=_current_gateway.get(),
        detail={"path": body.path, "access": body.access},
    )
    return result


@router.delete(
    "/sandboxes/{name}/policy/filesystem",
    dependencies=[Depends(require_role("operator"))],
)
async def delete_filesystem_path(
    name: str,
    request: Request,
    path: str = Query(..., description="Filesystem path to delete"),
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Delete a filesystem path (pass path as query param)."""
    result = await asyncio.to_thread(svc.delete_filesystem_path, name, path)
    logger.info(
        "Filesystem path deleted (sandbox=%s, actor=%s)",
        name,
        get_actor(request),
    )
    await audit_log(
        request,
        "policy.filesystem.delete",
        "policy",
        name,
        gateway=_current_gateway.get(),
        detail={"path": path},
    )
    return result


# ─── Process/Landlock Update ─────────────────────────────────────────


@router.put(
    "/sandboxes/{name}/policy/process",
    dependencies=[Depends(require_role("operator"))],
)
async def update_process_policy(
    name: str,
    body: ProcessPolicyRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Update process and landlock settings."""
    result = await asyncio.to_thread(
        svc.update_process_policy,
        name,
        run_as_user=body.run_as_user,
        run_as_group=body.run_as_group,
        landlock_compatibility=body.landlock_compatibility,
    )
    logger.info(
        "Process policy updated (sandbox=%s, actor=%s)",
        name,
        get_actor(request),
    )
    await audit_log(
        request,
        "policy.process.update",
        "policy",
        name,
        gateway=_current_gateway.get(),
    )
    return result


# ─── Presets (global, not gateway-scoped) ────────────────────────────────────
# preset_router is mounted separately at the global level (/api/policies/*)
# so that preset listing works without a gateway context.

preset_router = APIRouter()


@preset_router.get("/policies/presets")
async def list_presets() -> list[dict[str, str]]:
    """List available policy presets (local YAML files, no gateway needed)."""
    return _list_presets()


@preset_router.get("/policies/presets/{preset_name}")
async def get_preset(preset_name: str) -> dict[str, Any]:
    """Load a single policy preset by name."""
    result = _get_preset(preset_name)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Preset '{preset_name}' not found",
        )
    return result


# Apply-preset stays on the gateway-scoped router (needs a sandbox context).
@router.post(
    "/sandboxes/{name}/policy/presets/{preset_name}",
    dependencies=[Depends(require_role("operator"))],
)
async def apply_preset(
    name: str,
    preset_name: str,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Apply a policy preset to a sandbox."""
    result = await asyncio.to_thread(svc.apply_preset, name, preset_name)
    logger.info(
        "Preset applied (sandbox=%s, preset=%s, actor=%s)",
        name,
        preset_name,
        get_actor(request),
    )
    await audit_log(
        request,
        "policy.preset.apply",
        "policy",
        name,
        gateway=_current_gateway.get(),
        detail={"preset": preset_name},
    )
    return result
