"""REST endpoints for sandbox policy management.

This module is the single entry point for every policy-touching
flow: reading the active and effective policy, listing and
diffing revisions, atomic single-rule CRUD for network /
filesystem / process sections, preset application, YAML
export / diff / apply for the GitOps flow, and pin CRUD for the
change-freeze flow.

Every write path checks for an active policy pin before touching
storage and translates
:class:`~shoreguard.exceptions.PolicyLockedError` into HTTP 423
so callers see a clean locked response instead of a masked
error. The GitOps apply path additionally consults the approval
workflow service and may return HTTP 202 ``vote_recorded`` when
a sandbox has a quorum workflow attached.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import shoreguard.services.approval_workflow as _wf_mod
import shoreguard.services.policy_apply_proposal as _apply_mod
import shoreguard.services.policy_pin as _pin_mod
from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import (
    PolicyAnalysisRequest,
    PolicyAnalysisResponse,
    PolicyApplyRequest,
    PolicyApplyResponse,
    PolicyDiffResponse,
    PolicyExportResponse,
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
from shoreguard.services.policy_diff import diff_policy, is_empty
from shoreguard.services.policy_diff import summary as diff_summary
from shoreguard.services.policy_merge_ops import (
    UnsupportedMergeError,
    compute_merge_operations,
)
from shoreguard.services.policy_yaml import (
    PolicyYamlError,
    parse_yaml,
    render_yaml,
    yaml_fingerprint,
)
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


# ─── GitOps: YAML export + apply ─────────────────────────────────────────────


def _extract_hash(snapshot: dict[str, Any]) -> str:
    """Pull the policy_hash out of a PolicyManager.get() snapshot.

    Args:
        snapshot: PolicyManager.get() return value.

    Returns:
        str: policy_hash, or empty string if absent.
    """
    revision = snapshot.get("revision") or {}
    return revision.get("policy_hash") or ""


def _extract_version(snapshot: dict[str, Any]) -> int:
    """Pull the active_version out of a snapshot.

    Args:
        snapshot: PolicyManager.get() return value.

    Returns:
        int: active_version, or 0 if absent.
    """
    return int(snapshot.get("active_version") or 0)


@router.get(
    "/sandboxes/{name}/policy/export",
    response_model=PolicyExportResponse,
)
async def export_policy(
    name: str,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Export a sandbox policy as a deterministic YAML document.

    Returns a ``metadata`` + ``policy`` YAML body. Policy pins do
    not block export — it is a pure read and the caller is expected
    to check the result into Git or feed it back to ``POST
    /policy/apply``. The ``policy_hash`` field in the metadata is
    the etag used by apply's optimistic locking, so a round-trip
    export → edit → apply cannot silently clobber a concurrent
    change made through another path.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        dict[str, Any]: ``PolicyExportResponse`` payload.
    """
    snapshot = await asyncio.to_thread(svc.get, name)
    policy = snapshot.get("policy") or {}
    gw = get_gateway_name(request)
    yaml_text = render_yaml(
        policy,
        gateway=gw,
        sandbox=name,
        version=_extract_version(snapshot),
        policy_hash=_extract_hash(snapshot),
    )
    await audit_log(request, "policy.exported", "policy", name, gateway=gw)
    return {
        "yaml": yaml_text,
        "gateway": gw,
        "sandbox": name,
        "version": _extract_version(snapshot),
        "policy_hash": _extract_hash(snapshot),
    }


@router.post(
    "/sandboxes/{name}/policy/apply",
    response_model=PolicyApplyResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def apply_policy(
    name: str,
    body: PolicyApplyRequest,
    request: Request,
    svc: PolicyService = Depends(_get_policy_service),
) -> Any:
    """Apply a YAML policy document to a sandbox.

    Body fields: ``yaml`` (required), ``dry_run`` (default false),
    ``expected_version`` (optional optimistic-lock etag — falls
    back to ``metadata.policy_hash`` in the YAML body).

    Response status values: ``up_to_date`` and ``dry_run`` return
    HTTP 200 with the diff; a fresh write returns HTTP 200
    ``applied``; when an approval workflow is active the first
    call records a vote and returns HTTP 202 ``vote_recorded``; a
    version mismatch returns HTTP 409 with the live hash so CI can
    refetch + retry; a pinned sandbox returns HTTP 423; malformed
    YAML returns HTTP 400.

    Args:
        name: Sandbox name.
        body: Apply request body.
        request: Incoming HTTP request.
        svc: Injected policy service.

    Returns:
        Any: ``PolicyApplyResponse`` dict, or ``JSONResponse`` for 202/409.

    Raises:
        HTTPException: 400 malformed YAML, 403 role not allowed,
            409 reject vote, 423 pinned.
    """
    check_write_rate_limit(request)
    _check_policy_pin(request, name)

    try:
        new_policy, metadata = parse_yaml(body.yaml)
    except PolicyYamlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    expected_version = body.expected_version or metadata.get("policy_hash")

    snapshot = await asyncio.to_thread(svc.get, name)
    current_policy = snapshot.get("policy") or {}
    current_hash = _extract_hash(snapshot)

    if expected_version and expected_version != current_hash:
        return JSONResponse(
            status_code=409,
            content={
                "status": "version_mismatch",
                "current_hash": current_hash,
                "expected_version": expected_version,
            },
        )

    diff = diff_policy(current_policy, new_policy)
    diff_is_empty = is_empty(diff)
    gw = get_gateway_name(request)

    if body.dry_run:
        await audit_log(
            request,
            "policy.apply.dry_run",
            "policy",
            name,
            gateway=gw,
            detail={"diff_summary": diff_summary(diff), "drift": not diff_is_empty},
        )
        return {
            "status": "dry_run",
            "current_hash": current_hash,
            "diff": diff,
        }

    if diff_is_empty:
        await audit_log(request, "policy.apply.noop", "policy", name, gateway=gw, detail={})
        return {
            "status": "up_to_date",
            "current_hash": current_hash,
            "diff": diff,
        }

    actor = get_actor(request)
    role = getattr(request.state, "role", None) or "viewer"
    chunk_id = f"policy.apply:{yaml_fingerprint(body.yaml)}"
    diff_summary_payload = diff_summary(diff)

    # ── Approval workflow gate ───────────────────────────────────────────
    wf_svc = _wf_mod.approval_workflow_service
    workflow = wf_svc.get_workflow(gw, name) if wf_svc is not None else None
    if wf_svc is not None and workflow is not None:
        proposal_svc = _apply_mod.policy_apply_proposal_service
        if proposal_svc is not None:
            await asyncio.to_thread(
                proposal_svc.upsert,
                gw,
                name,
                chunk_id,
                yaml_text=body.yaml,
                expected_hash=current_hash,
                proposed_by=actor,
            )
        try:
            vote = await asyncio.to_thread(
                wf_svc.record_decision,
                gw,
                name,
                chunk_id,
                actor=actor,
                role=role,
                decision="approve",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if not vote.quorum_met:
            await audit_log(
                request,
                "policy.apply.voted",
                "policy",
                name,
                gateway=gw,
                detail={
                    "chunk_id": chunk_id,
                    "votes_needed": vote.votes_needed,
                    "diff_summary": diff_summary_payload,
                },
            )
            await fire_webhook(
                "approval.vote_cast",
                {
                    "sandbox": name,
                    "gateway": gw,
                    "actor": actor,
                    "chunk_id": chunk_id,
                    "scope": "policy.apply",
                },
            )
            approve_votes = sum(1 for d in vote.decisions if d["decision"] == "approve")
            return JSONResponse(
                status_code=202,
                content={
                    "status": "vote_recorded",
                    "current_hash": current_hash,
                    "diff": diff,
                    "votes_needed": vote.votes_needed,
                    "votes_cast": approve_votes,
                    "chunk_id": chunk_id,
                },
            )

        # Quorum met — clear proposal, fall through to write
        if proposal_svc is not None:
            await asyncio.to_thread(proposal_svc.delete, gw, name, chunk_id)
        await fire_webhook(
            "approval.quorum_met",
            {
                "sandbox": name,
                "gateway": gw,
                "chunk_id": chunk_id,
                "scope": "policy.apply",
                "votes_needed": workflow["required_approvals"],
            },
        )

    # ── Write branch ─────────────────────────────────────────────────────
    merge_ops: list[dict[str, Any]] | None = None
    if body.mode == "merge":
        try:
            merge_ops = compute_merge_operations(current_policy, new_policy)
        except UnsupportedMergeError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "status": "merge_unsupported",
                    "reason": str(exc),
                    "hint": "retry with mode='replace' for changes outside network_policies",
                },
            ) from exc
        result = await asyncio.to_thread(svc.update_merge, name, merge_ops)
    else:
        result = await asyncio.to_thread(svc.update, name, new_policy)
    new_hash = ""
    if isinstance(result, dict):
        rev = result.get("revision") or {}
        new_hash = rev.get("policy_hash") or ""
    await audit_log(
        request,
        "policy.applied",
        "policy",
        name,
        gateway=gw,
        detail={
            "chunk_id": chunk_id,
            "expected_version": expected_version,
            "applied_version": new_hash,
            "diff_summary": diff_summary_payload,
            "via_workflow": workflow is not None,
            "apply_mode": body.mode,
            "merge_operation_count": (len(merge_ops) if merge_ops is not None else None),
        },
    )
    await fire_webhook(
        "policy.applied",
        {
            "sandbox": name,
            "gateway": gw,
            "actor": actor,
            "applied_version": new_hash,
            "diff_summary": diff_summary_payload,
        },
    )
    return {
        "status": "applied",
        "current_hash": current_hash,
        "applied_version": new_hash,
        "diff": diff,
    }


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


# ─── Policy Pinning ──────────────────────────────────────────────────────────


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
