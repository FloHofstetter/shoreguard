"""REST endpoints for sandbox boot hooks (M22).

CRUD + manual trigger + reorder for the per-sandbox boot hook table.
The actual execution surface lives in
:class:`~shoreguard.services.boot_hooks.BootHookService`; this module is
only the HTTP wrapper.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_gateway_name
from shoreguard.exceptions import ValidationError
from shoreguard.services.audit import audit_log

if TYPE_CHECKING:
    from shoreguard.services.boot_hooks import BootHookService

logger = logging.getLogger(__name__)

router = APIRouter()


def _service() -> BootHookService:
    """Return the global ``BootHookService`` singleton.

    Returns:
        BootHookService: The active boot hook service instance.

    Raises:
        HTTPException: ``503`` if the service has not been initialised.
    """
    from shoreguard.services import boot_hooks as bh_mod

    if bh_mod.boot_hook_service is None:
        raise HTTPException(503, "BootHookService not initialised")
    return bh_mod.boot_hook_service


class BootHookCreate(BaseModel):
    """Body for creating a boot hook.

    Attributes:
        name: Human-readable hook name (unique per sandbox+phase).
        phase: ``pre_create`` or ``post_create``.
        command: Shell command (parsed via shlex on execution).
        workdir: Optional working directory (post-create only).
        env: Optional extra environment variables.
        timeout_seconds: Wall-clock timeout for the hook (1–600).
        order: Optional sort key; defaults to next free slot.
        enabled: Whether the hook participates in automatic runs.
        continue_on_failure: If true, post-create failures don't abort
            subsequent hooks.
    """

    name: str = Field(min_length=1, max_length=128)
    phase: str = Field(pattern="^(pre_create|post_create)$")
    command: str = Field(min_length=1, max_length=8192)
    workdir: str = Field(default="", max_length=512)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    order: int | None = None
    enabled: bool = True
    continue_on_failure: bool = False


class BootHookUpdate(BaseModel):
    """Body for patching a boot hook (all fields optional).

    Attributes:
        command: New shell command, if provided.
        workdir: New working directory, if provided.
        env: New environment variables, if provided.
        timeout_seconds: New wall-clock timeout, if provided.
        order: New sort order, if provided.
        enabled: New enabled flag, if provided.
        continue_on_failure: New continue-on-failure flag, if provided.
    """

    command: str | None = Field(default=None, max_length=8192)
    workdir: str | None = Field(default=None, max_length=512)
    env: dict[str, str] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    order: int | None = None
    enabled: bool | None = None
    continue_on_failure: bool | None = None


class ReorderRequest(BaseModel):
    """Body for reordering hooks within a phase.

    Attributes:
        phase: ``pre_create`` or ``post_create``.
        hook_ids: Hook IDs in their new order.
    """

    phase: str = Field(pattern="^(pre_create|post_create)$")
    hook_ids: list[int] = Field(min_length=0)


@router.get(
    "/{name}/hooks",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def list_hooks(
    name: str,
    gw: str = Depends(get_gateway_name),
    phase: str | None = None,
) -> dict[str, Any]:
    """List boot hooks for a sandbox, optionally filtered by phase.

    Args:
        name: Sandbox name from the URL path.
        gw: Gateway name from the URL path.
        phase: Optional ``pre_create`` / ``post_create`` filter.

    Returns:
        dict[str, Any]: ``{items}``.

    Raises:
        HTTPException: ``400`` if ``phase`` is provided and invalid.
    """
    try:
        items = _service().list(gw, name, phase=phase)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"items": items}


@router.get(
    "/{name}/hooks/{hook_id}",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def get_hook(
    name: str,
    hook_id: int,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Get a single boot hook by id.

    Args:
        name: Sandbox name from the URL path.
        hook_id: Hook primary key.
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: Hook record.

    Raises:
        HTTPException: ``404`` if missing or owned by another sandbox.
    """
    hook = _service().get(hook_id)
    if hook is None or hook["gateway_name"] != gw or hook["sandbox_name"] != name:
        raise HTTPException(404, "Boot hook not found")
    return hook


@router.post(
    "/{name}/hooks",
    dependencies=[Depends(require_role("admin"))],
    response_model=None,
)
async def create_hook(
    name: str,
    body: BootHookCreate,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Create a new boot hook.

    Args:
        name: Sandbox name from the URL path.
        body: Hook payload.
        request: Incoming HTTP request (used for audit context).
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: The created hook.

    Raises:
        HTTPException: ``400`` on validation failure or duplicate name.
        Exception: For unexpected backend errors not classified as
            validation/uniqueness failures.
    """
    actor = get_actor(request)
    try:
        hook = _service().create(
            gateway_name=gw,
            sandbox_name=name,
            name=body.name,
            phase=body.phase,
            command=body.command,
            actor=actor,
            workdir=body.workdir,
            env=body.env,
            timeout_seconds=body.timeout_seconds,
            order=body.order,
            enabled=body.enabled,
            continue_on_failure=body.continue_on_failure,
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface as 400 for clarity
        if "UNIQUE" in str(exc) or "unique" in str(exc):
            raise HTTPException(
                400,
                f"A {body.phase} hook named '{body.name}' already exists for this sandbox",
            ) from exc
        raise
    await audit_log(
        request,
        "boot_hook.created",
        "sandbox",
        f"{gw}/{name}",
        gateway=gw,
        detail={"hook_id": hook["id"], "name": body.name, "phase": body.phase},
    )
    return hook


@router.put(
    "/{name}/hooks/{hook_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=None,
)
async def update_hook(
    name: str,
    hook_id: int,
    body: BootHookUpdate,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Patch an existing boot hook.

    Args:
        name: Sandbox name from the URL path.
        hook_id: Hook primary key.
        body: Patch payload (all fields optional).
        request: Incoming HTTP request.
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: Updated hook.

    Raises:
        HTTPException: ``404`` if missing, ``400`` on invalid input.
    """
    existing = _service().get(hook_id)
    if existing is None or existing["gateway_name"] != gw or existing["sandbox_name"] != name:
        raise HTTPException(404, "Boot hook not found")
    try:
        updated = _service().update(
            hook_id,
            command=body.command,
            workdir=body.workdir,
            env=body.env,
            timeout_seconds=body.timeout_seconds,
            order=body.order,
            enabled=body.enabled,
            continue_on_failure=body.continue_on_failure,
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "Boot hook not found")
    await audit_log(
        request,
        "boot_hook.updated",
        "sandbox",
        f"{gw}/{name}",
        gateway=gw,
        detail={"hook_id": hook_id},
    )
    return updated


@router.delete(
    "/{name}/hooks/{hook_id}",
    dependencies=[Depends(require_role("admin"))],
    response_model=None,
)
async def delete_hook(
    name: str,
    hook_id: int,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> Response:
    """Delete a boot hook.

    Args:
        name: Sandbox name from the URL path.
        hook_id: Hook primary key.
        request: Incoming HTTP request.
        gw: Gateway name from the URL path.

    Returns:
        Response: Empty ``204`` on success.

    Raises:
        HTTPException: ``404`` if no such hook.
    """
    existing = _service().get(hook_id)
    if existing is None or existing["gateway_name"] != gw or existing["sandbox_name"] != name:
        raise HTTPException(404, "Boot hook not found")
    _service().delete(hook_id)
    await audit_log(
        request,
        "boot_hook.deleted",
        "sandbox",
        f"{gw}/{name}",
        gateway=gw,
        detail={"hook_id": hook_id},
    )
    return Response(status_code=204)


@router.post(
    "/{name}/hooks/reorder",
    dependencies=[Depends(require_role("admin"))],
    response_model=None,
)
async def reorder_hooks(
    name: str,
    body: ReorderRequest,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Reorder hooks within a phase.

    Args:
        name: Sandbox name from the URL path.
        body: Phase + new ordering.
        request: Incoming HTTP request.
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: ``{items}`` in new order.

    Raises:
        HTTPException: ``400`` if the id set does not match.
    """
    try:
        items = _service().reorder(gw, name, body.phase, body.hook_ids)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    await audit_log(
        request,
        "boot_hook.reordered",
        "sandbox",
        f"{gw}/{name}",
        gateway=gw,
        detail={"phase": body.phase, "count": len(body.hook_ids)},
    )
    return {"items": items}


@router.post(
    "/{name}/hooks/{hook_id}/run",
    dependencies=[Depends(require_role("operator"))],
    response_model=None,
)
async def run_hook(
    name: str,
    hook_id: int,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Manually trigger a single boot hook.

    For pre-create hooks, the hook runs locally with an empty spec
    (caller is responsible for understanding the limited environment).
    For post-create hooks, the hook executes inside the live sandbox.

    Args:
        name: Sandbox name from the URL path.
        hook_id: Hook primary key.
        request: Incoming HTTP request.
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: ``HookResult`` from the execution.

    Raises:
        HTTPException: ``404`` if missing.
    """
    existing = _service().get(hook_id)
    if existing is None or existing["gateway_name"] != gw or existing["sandbox_name"] != name:
        raise HTTPException(404, "Boot hook not found")
    result = _service().run_one(hook_id)
    await audit_log(
        request,
        "boot_hook.manual_run",
        "sandbox",
        f"{gw}/{name}",
        gateway=gw,
        detail={
            "hook_id": hook_id,
            "status": (result or {}).get("status"),
        },
    )
    if result is None:
        raise HTTPException(404, "Boot hook not found")
    return result
