"""REST endpoints for policy management."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

import shoreguard.services.policy_pin as _pin_mod
from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import (
    PolicyAnalysisRequest,
    PolicyAnalysisResponse,
    PolicyDiffResponse,
    PolicyPinRequest,
    PolicyPinResponse,
    PolicyResponse,
    PresetSummaryResponse,
)
from shoreguard.api.validation import check_write_rate_limit
from shoreguard.client import ShoreGuardClient
from shoreguard.exceptions import PolicyLockedError
from shoreguard.presets import get_preset as _get_preset
from shoreguard.presets import list_presets as _list_presets
from shoreguard.services.audit import audit_log
from shoreguard.services.policy import PolicyService
from shoreguard.services.webhooks import fire_webhook

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_policy_pin(request: Request, sandbox_name: str) -> None:
    """Raise HTTP 423 if the sandbox's policy is pinned.

    Args:
        request: Incoming HTTP request (for gateway name).
        sandbox_name: Sandbox to check.

    Raises:
        HTTPException: 423 Locked if an active pin exists.
    """
    svc = _pin_mod.policy_pin_service
    if svc is None:
        return
    try:
        svc.check_pin(get_gateway_name(request), sandbox_name)
    except PolicyLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc


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

    key: str = Field(min_length=1, max_length=253)
    rule: dict[str, Any]


class FilesystemPathRequest(BaseModel):
    """Body for adding a filesystem path.

    Attributes:
        path: Filesystem path to allow.
        access: Access mode, either read-only or read-write.
    """

    path: str = Field(min_length=1, max_length=4096)
    access: Literal["ro", "rw"]


class ProcessPolicyRequest(BaseModel):
    """Body for updating process/landlock settings.

    Attributes:
        run_as_user: User to run processes as.
        run_as_group: Group to run processes as.
        landlock_compatibility: Landlock compatibility mode.
    """

    run_as_user: str | None = Field(default=None, max_length=253)
    run_as_group: str | None = Field(default=None, max_length=253)
    landlock_compatibility: str | None = Field(default=None, max_length=50)


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


@router.get("/sandboxes/{name}/policy/effective", response_model=PolicyResponse)
async def get_effective_policy(
    name: str,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Get the effective policy — what the gateway currently enforces.

    Returns the same envelope as ``GET /policy`` (active_version, revision,
    policy), plus a ``source: "gateway_runtime"`` marker. In today's
    architecture presets are merged eagerly into the declared policy at
    apply time, so the stored policy already is the fully resolved
    document — this endpoint is the stable contract the UI should use when
    it wants "what is actually being enforced" rather than "what was last
    PUT".

    Args:
        name: Sandbox name.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Effective policy envelope.
    """
    return await asyncio.to_thread(svc.get_effective, name)


@router.post(
    "/sandboxes/{name}/policy/analysis",
    response_model=PolicyAnalysisResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def submit_policy_analysis(
    name: str,
    body: PolicyAnalysisRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Submit denial analysis results and proposed policy chunks to the gateway.

    Pass-through to the OpenShell ``SubmitPolicyAnalysis`` RPC. Used by
    external analyzers (LLM-backed or rule-based) that observe sandbox
    denials, propose policy chunks that would fix them, and submit the
    bundle for the gateway to merge into the draft policy. The gateway
    decides accept/reject per chunk and returns counters plus
    rejection reasons.

    Args:
        name: Target sandbox name.
        body: Request envelope with ``summaries``, ``proposed_chunks``,
            and optional ``analysis_mode``.
        request: Incoming HTTP request (for rate limiting + audit).
        svc: Injected policy service.

    Returns:
        dict[str, Any]: ``{"accepted_chunks": int, "rejected_chunks":
        int, "rejection_reasons": list[str]}``.
    """
    check_write_rate_limit(request)
    result = await asyncio.to_thread(
        svc.submit_analysis,
        name,
        summaries=body.summaries,
        proposed_chunks=body.proposed_chunks,
        analysis_mode=body.analysis_mode,
    )
    logger.info(
        "Policy analysis submitted (sandbox=%s, actor=%s, accepted=%d, rejected=%d)",
        name,
        get_actor(request),
        result["accepted_chunks"],
        result["rejected_chunks"],
    )
    await audit_log(
        request,
        "sandbox.policy.analyze",
        "sandbox",
        name,
        gateway=get_gateway_name(request),
        detail={
            "analysis_mode": body.analysis_mode,
            "summary_count": len(body.summaries),
            "chunk_count": len(body.proposed_chunks),
            "accepted": result["accepted_chunks"],
            "rejected": result["rejected_chunks"],
        },
    )
    return result


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
    _check_policy_pin(request, name)
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
    check_write_rate_limit(request)
    _check_policy_pin(request, name)
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
    _check_policy_pin(request, name)
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
    check_write_rate_limit(request)
    _check_policy_pin(request, name)
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
    _check_policy_pin(request, name)
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
    check_write_rate_limit(request)
    _check_policy_pin(request, name)
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


# ─── Policy Pinning (M18) ────────────────────────────────────────────────────


@router.get("/sandboxes/{name}/policy/pin", response_model=PolicyPinResponse)
async def get_policy_pin(
    name: str,
    request: Request,
) -> dict[str, Any]:
    """Get the active policy pin for a sandbox.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Pin data.

    Raises:
        HTTPException: 404 if no active pin exists.
    """
    if _pin_mod.policy_pin_service is None:
        raise HTTPException(status_code=503, detail="Policy pin service not initialised")
    pin = _pin_mod.policy_pin_service.get_pin(get_gateway_name(request), name)
    if pin is None:
        raise HTTPException(status_code=404, detail="No active pin for this sandbox")
    return pin


@router.post(
    "/sandboxes/{name}/policy/pin",
    response_model=PolicyPinResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def pin_policy(
    name: str,
    request: Request,
    body: PolicyPinRequest | None = None,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Pin the sandbox's current policy version.

    Reads the active policy version from the gateway and locks it.
    While pinned, all policy updates and draft approvals are blocked.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.
        body: Optional pin metadata (reason, expiry).
        svc: Injected policy service.

    Returns:
        dict[str, Any]: Created pin data.

    Raises:
        HTTPException: 503 if service not initialised, 400 if version unreadable.
    """
    import datetime

    if _pin_mod.policy_pin_service is None:
        raise HTTPException(status_code=503, detail="Policy pin service not initialised")

    # Read current version from gateway
    current = await asyncio.to_thread(svc.get, name)
    version = current.get("active_version")
    if version is None:
        raise HTTPException(status_code=400, detail="Could not read active policy version")

    actor = get_actor(request)
    gw = get_gateway_name(request)

    expires_at = None
    reason = None
    if body:
        reason = body.reason
        if body.expires_at:
            expires_at = datetime.datetime.fromisoformat(body.expires_at)

    pin = _pin_mod.policy_pin_service.pin(
        gw, name, version, actor, reason=reason, expires_at=expires_at
    )
    logger.info("Policy pinned (sandbox=%s, version=%d, actor=%s)", name, version, actor)
    await audit_log(
        request,
        "policy.pinned",
        "policy",
        name,
        gateway=gw,
        detail={"pinned_version": version, "reason": reason},
    )
    await fire_webhook(
        "policy.pinned",
        {"sandbox": name, "gateway": gw, "version": version, "actor": actor},
    )
    return pin


@router.delete(
    "/sandboxes/{name}/policy/pin",
    status_code=204,
    dependencies=[Depends(require_role("operator"))],
)
async def unpin_policy(
    name: str,
    request: Request,
) -> None:
    """Remove the policy pin for a sandbox.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.

    Raises:
        HTTPException: 404 if no active pin exists.
    """
    if _pin_mod.policy_pin_service is None:
        raise HTTPException(status_code=503, detail="Policy pin service not initialised")
    gw = get_gateway_name(request)
    removed = _pin_mod.policy_pin_service.unpin(gw, name)
    if not removed:
        raise HTTPException(status_code=404, detail="No active pin for this sandbox")
    actor = get_actor(request)
    logger.info("Policy unpinned (sandbox=%s, actor=%s)", name, actor)
    await audit_log(
        request,
        "policy.unpinned",
        "policy",
        name,
        gateway=gw,
    )
    await fire_webhook(
        "policy.unpinned",
        {"sandbox": name, "gateway": gw, "actor": actor},
    )


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
    _check_policy_pin(request, name)
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
