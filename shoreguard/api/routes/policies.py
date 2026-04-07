"""REST endpoints for policy management."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import PolicyDiffResponse, PolicyResponse, PresetSummaryResponse
from shoreguard.client import ShoreGuardClient
from shoreguard.presets import get_preset as _get_preset
from shoreguard.presets import list_presets as _list_presets
from shoreguard.services.audit import audit_log
from shoreguard.services.policy import PolicyService
from shoreguard.services.webhooks import fire_webhook

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_policy_service(client: ShoreGuardClient = Depends(get_client)) -> PolicyService:
    """Build a PolicyService from the injected client.

    Args:
        client: gRPC client for the active gateway.

    Returns:
        PolicyService: Service instance bound to the client.
    """
    return PolicyService(client)


class NetworkRuleRequest(BaseModel):
    """Body for adding/updating a network rule.

    Attributes:
        key: Unique rule identifier.
        rule: Rule definition as a dict.
    """

    key: str
    rule: dict[str, Any]


class FilesystemPathRequest(BaseModel):
    """Body for adding a filesystem path.

    Attributes:
        path: Filesystem path to allow.
        access: Access mode, either read-only or read-write.
    """

    path: str
    access: Literal["ro", "rw"]


class ProcessPolicyRequest(BaseModel):
    """Body for updating process/landlock settings.

    Attributes:
        run_as_user: User to run processes as.
        run_as_group: Group to run processes as.
        landlock_compatibility: Landlock compatibility mode.
    """

    run_as_user: str | None = None
    run_as_group: str | None = None
    landlock_compatibility: str | None = None


@router.get("/sandboxes/{name}/policy", response_model=PolicyResponse)
async def get_policy(
    name: str,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Get the active policy for a sandbox.

    Args:
        name: Sandbox name.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Active policy document.
    """
    return await asyncio.to_thread(svc.get, name)


@router.put(
    "/sandboxes/{name}/policy",
    response_model=PolicyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def update_policy(
    name: str,
    body: dict[str, Any],
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Push a new policy version to a sandbox.

    Args:
        name: Sandbox name.
        body: Full policy document to apply.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Updated policy record.
    """
    result = await asyncio.to_thread(svc.update, name, body)
    logger.info(
        "Policy updated (sandbox=%s, actor=%s)",
        name,
        get_actor(request),
    )
    gw = get_gateway_name(request)
    await audit_log(request, "policy.update", "policy", name, gateway=gw)
    await fire_webhook(
        "policy.updated",
        {"sandbox": name, "gateway": gw, "actor": get_actor(request)},
    )
    return result


@router.get("/sandboxes/{name}/policy/revisions")
async def list_policy_revisions(
    name: str,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    svc: PolicyService = Depends(_get_policy_service),
) -> list[dict[str, Any]]:
    """List policy revision history for a sandbox.

    Args:
        name: Sandbox name.
        limit: Maximum number of revisions to return.
        offset: Number of revisions to skip.
        svc: Injected policy service.

    Returns:
        list[dict[str, Any]]: Policy revision records.
    """
    return await asyncio.to_thread(
        svc.list_revisions,
        name,
        limit=limit,
        offset=offset,
    )


@router.get("/sandboxes/{name}/policy/diff", response_model=PolicyDiffResponse)
async def diff_policy_revisions(
    name: str,
    version_a: int = Query(..., ge=1),
    version_b: int = Query(..., ge=1),
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Compare two policy revisions side-by-side.

    Args:
        name: Sandbox name.
        version_a: First revision number.
        version_b: Second revision number.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Diff result with added, removed, and changed entries.
    """
    return await asyncio.to_thread(svc.diff_revisions, name, version_a, version_b)


# ─── Network Rule CRUD ───────────────────────────────────────────────────────


@router.post(
    "/sandboxes/{name}/policy/network-rules",
    response_model=PolicyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def add_network_rule(
    name: str,
    body: NetworkRuleRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Add or update a single network rule.

    Args:
        name: Sandbox name.
        body: Network rule payload with key and rule definition.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Updated policy after rule addition.
    """
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
        gateway=get_gateway_name(request),
        detail={"key": body.key},
    )
    return result


@router.delete(
    "/sandboxes/{name}/policy/network-rules/{key}",
    response_model=PolicyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def delete_network_rule(
    name: str,
    key: str,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Delete a single network rule.

    Args:
        name: Sandbox name.
        key: Rule identifier to delete.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Updated policy after rule deletion.
    """
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
        gateway=get_gateway_name(request),
        detail={"key": key},
    )
    return result


# ─── Filesystem Path CRUD ─────────────────────────────────────────


@router.post(
    "/sandboxes/{name}/policy/filesystem",
    response_model=PolicyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def add_filesystem_path(
    name: str,
    body: FilesystemPathRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Add or update a filesystem path.

    Args:
        name: Sandbox name.
        body: Filesystem path payload with path and access mode.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Updated policy after path addition.
    """
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
        gateway=get_gateway_name(request),
        detail={"path": body.path, "access": body.access},
    )
    return result


@router.delete(
    "/sandboxes/{name}/policy/filesystem",
    response_model=PolicyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def delete_filesystem_path(
    name: str,
    request: Request,
    path: str = Query(..., description="Filesystem path to delete"),
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Delete a filesystem path (pass path as query param).

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.
        path: Filesystem path to remove.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Updated policy after path deletion.
    """
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
        gateway=get_gateway_name(request),
        detail={"path": path},
    )
    return result


# ─── Process/Landlock Update ─────────────────────────────────────────


@router.put(
    "/sandboxes/{name}/policy/process",
    response_model=PolicyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def update_process_policy(
    name: str,
    body: ProcessPolicyRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Update process and landlock settings.

    Args:
        name: Sandbox name.
        body: Process policy payload.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Updated policy record.
    """
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
        gateway=get_gateway_name(request),
    )
    return result


# ─── Presets (global, not gateway-scoped) ────────────────────────────────────
# preset_router is mounted separately at the global level (/api/policies/*)
# so that preset listing works without a gateway context.

preset_router = APIRouter()


@preset_router.get("/policies/presets", response_model=list[PresetSummaryResponse])
async def list_presets() -> list[dict[str, str]]:
    """List available policy presets (local YAML files, no gateway needed).

    Returns:
        list[dict[str, str]]: Preset name and description pairs.
    """
    return _list_presets()


@preset_router.get("/policies/presets/{preset_name}")
async def get_preset(preset_name: str) -> dict[str, Any]:
    """Load a single policy preset by name.

    Args:
        preset_name: Name of the preset to load.

    Returns:
        dict[str, Any]: Preset policy document.

    Raises:
        HTTPException: If the preset is not found.
    """
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
    response_model=PolicyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def apply_preset(
    name: str,
    preset_name: str,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Apply a policy preset to a sandbox.

    Args:
        name: Sandbox name.
        preset_name: Name of the preset to apply.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Updated policy after preset application.
    """
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
        gateway=get_gateway_name(request),
        detail={"preset": preset_name},
    )
    return result
