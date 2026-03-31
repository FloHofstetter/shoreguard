"""REST endpoints for provider CRUD."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from shoreguard.api.auth import require_role
from shoreguard.api.deps import _current_gateway, get_actor, get_client
from shoreguard.client import ShoreGuardClient
from shoreguard.services.audit import audit_log
from shoreguard.services.providers import ProviderService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_provider_service(client: ShoreGuardClient = Depends(get_client)) -> ProviderService:
    return ProviderService(client)


class CreateProviderRequest(BaseModel):
    """Body for creating a new provider."""

    name: str
    type: str
    api_key: str = ""
    credentials: dict[str, str] = {}
    config: dict[str, str] = {}


class UpdateProviderRequest(BaseModel):
    """Body for updating a provider."""

    type: str = ""
    credentials: dict[str, str] = {}
    config: dict[str, str] = {}


@router.get("/types")
async def list_provider_types() -> list[dict[str, str]]:
    """List known provider types with metadata (label, icon, cred_key)."""
    return ProviderService.list_known_types()


@router.get("/inference-providers")
async def list_inference_providers() -> list[dict[str, str]]:
    """List known inference provider options."""
    return ProviderService.list_inference_providers()


@router.get("/community-sandboxes")
async def list_community_sandboxes() -> list[dict[str, Any]]:
    """List community sandbox templates from openshell.yaml."""
    return ProviderService.list_community_sandboxes()


@router.get("")
async def list_providers(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    svc: ProviderService = Depends(_get_provider_service),
) -> list[dict[str, Any]]:
    """List all providers."""
    return await asyncio.to_thread(svc.list, limit=limit, offset=offset)


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_role("operator"))],
)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """Create a new provider."""
    result = await asyncio.to_thread(
        svc.create,
        name=body.name,
        provider_type=body.type,
        api_key=body.api_key,
        extra_credentials=body.credentials or None,
        config=body.config or None,
    )
    logger.info(
        "Provider created (provider=%s, actor=%s)",
        body.name,
        get_actor(request),
    )
    await audit_log(
        request,
        "provider.create",
        "provider",
        body.name,
        gateway=_current_gateway.get(),
        detail={"type": body.type},
    )
    return result


@router.get("/{name}")
async def get_provider(
    name: str,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """Get a provider by name."""
    return await asyncio.to_thread(svc.get, name)


@router.put(
    "/{name}",
    dependencies=[Depends(require_role("operator"))],
)
async def update_provider(
    name: str,
    body: UpdateProviderRequest,
    request: Request,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """Update an existing provider."""
    result = await asyncio.to_thread(
        svc.update,
        name=name,
        provider_type=body.type,
        credentials=body.credentials or None,
        config=body.config or None,
    )
    logger.info(
        "Provider updated (provider=%s, actor=%s)",
        name,
        get_actor(request),
    )
    await audit_log(request, "provider.update", "provider", name, gateway=_current_gateway.get())
    return result


@router.delete(
    "/{name}",
    dependencies=[Depends(require_role("operator"))],
)
async def delete_provider(
    name: str,
    request: Request,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, bool]:
    """Delete a provider by name."""
    deleted = await asyncio.to_thread(svc.delete, name)
    if deleted:
        logger.info(
            "Provider deleted (provider=%s, actor=%s)",
            name,
            get_actor(request),
        )
        await audit_log(
            request, "provider.delete", "provider", name, gateway=_current_gateway.get()
        )
    return {"deleted": deleted}
