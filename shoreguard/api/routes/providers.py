"""REST endpoints for provider CRUD."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import (
    PaginatedResponse,
    ProviderDeleteResponse,
    ProviderEnvResponse,
    ProviderResponse,
    ProviderTypeResponse,
)
from shoreguard.api.validation import check_write_rate_limit
from shoreguard.client import ShoreGuardClient
from shoreguard.services.audit import audit_log
from shoreguard.services.providers import ProviderService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_provider_service(client: ShoreGuardClient = Depends(get_client)) -> ProviderService:
    """Build a ProviderService from the injected client.

    Args:
        client: gRPC client for the active gateway.

    Returns:
        ProviderService: Service instance bound to the client.
    """
    return ProviderService(client)


class CreateProviderRequest(BaseModel):
    """Body for creating a new provider.

    Attributes:
        name: Provider name.
        type: Provider type identifier.
        api_key: Optional API key for the provider.
        credentials: Extra credential key-value pairs.
        config: Additional configuration key-value pairs.
    """

    name: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    type: str = Field(min_length=1, max_length=100)
    api_key: str = Field(default="", max_length=512)
    credentials: dict[str, str] = Field(default_factory=dict)
    config: dict[str, str] = Field(default_factory=dict)

    @field_validator("credentials", "config")
    @classmethod
    def check_dict_size(cls, v: dict[str, str]) -> dict[str, str]:
        """Enforce entry count and key/value length limits.

        Args:
            v: Dictionary value to validate.

        Returns:
            dict[str, str]: The validated dictionary, unchanged.

        Raises:
            ValueError: If there are too many entries or keys/values exceed length limits.
        """
        if len(v) > 50:
            raise ValueError("too many entries (max 50)")
        for k, val in v.items():
            if len(k) > 256 or len(val) > 8192:
                raise ValueError("key max 256 chars, value max 8192 chars")
        return v


class UpdateProviderRequest(BaseModel):
    """Body for updating a provider.

    Attributes:
        type: Provider type identifier.
        credentials: Credential key-value pairs to update.
        config: Configuration key-value pairs to update.
    """

    type: str = Field(default="", max_length=100)
    credentials: dict[str, str] = Field(default_factory=dict)
    config: dict[str, str] = Field(default_factory=dict)

    @field_validator("credentials", "config")
    @classmethod
    def check_dict_size(cls, v: dict[str, str]) -> dict[str, str]:
        """Enforce entry count and key/value length limits.

        Args:
            v: Dictionary value to validate.

        Returns:
            dict[str, str]: The validated dictionary, unchanged.

        Raises:
            ValueError: If there are too many entries or keys/values exceed length limits.
        """
        if len(v) > 50:
            raise ValueError("too many entries (max 50)")
        for k, val in v.items():
            if len(k) > 256 or len(val) > 8192:
                raise ValueError("key max 256 chars, value max 8192 chars")
        return v


@router.get("/types", response_model=list[ProviderTypeResponse])
async def list_provider_types() -> list[dict[str, str]]:
    """List known provider types with metadata (label, icon, cred_key).

    Returns:
        list[dict[str, str]]: Provider type definitions.
    """
    return ProviderService.list_known_types()


@router.get("/inference-providers")
async def list_inference_providers() -> list[dict[str, str]]:
    """List known inference provider options.

    Returns:
        list[dict[str, str]]: Inference provider definitions.
    """
    return ProviderService.list_inference_providers()


@router.get("/community-sandboxes")
async def list_community_sandboxes() -> list[dict[str, Any]]:
    """List community sandbox templates from openshell.yaml.

    Returns:
        list[dict[str, Any]]: Community sandbox template definitions.
    """
    return ProviderService.list_community_sandboxes()


@router.get("", response_model=PaginatedResponse)
async def list_providers(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """List all providers.

    Args:
        limit: Maximum number of results to return.
        offset: Number of results to skip.
        svc: Injected provider service.

    Returns:
        dict[str, Any]: Paginated provider records.
    """
    items = await asyncio.to_thread(svc.list, limit=limit, offset=offset)
    return {"items": items, "total": None}


@router.post(
    "",
    status_code=201,
    response_model=ProviderResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """Create a new provider.

    Args:
        body: Provider creation payload.
        request: Incoming HTTP request.
        svc: Injected provider service.

    Returns:
        dict[str, Any]: Created provider record.
    """
    check_write_rate_limit(request)
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
        gateway=get_gateway_name(request),
        detail={"type": body.type},
    )
    return result


@router.get("/{name}", response_model=ProviderResponse)
async def get_provider(
    name: str,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """Get a provider by name.

    Args:
        name: Provider name.
        svc: Injected provider service.

    Returns:
        dict[str, Any]: Provider record.
    """
    return await asyncio.to_thread(svc.get, name)


@router.get("/{name}/env", response_model=ProviderEnvResponse)
async def get_provider_env(
    name: str,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """Get the redacted environment projection for a provider.

    Returns the environment variables this provider injects into sandboxes
    without revealing any secret values. Useful for debugging agent
    misconfiguration — e.g. verifying that a provider's credentials are
    actually exposed under the expected variable name.

    Args:
        name: Provider name.
        svc: Injected provider service.

    Returns:
        dict[str, Any]: Provider env projection with keys and sources.
    """
    return await asyncio.to_thread(svc.get_env, name)


@router.put(
    "/{name}",
    response_model=ProviderResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def update_provider(
    name: str,
    body: UpdateProviderRequest,
    request: Request,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, Any]:
    """Update an existing provider.

    Args:
        name: Provider name.
        body: Provider update payload.
        request: Incoming HTTP request.
        svc: Injected provider service.

    Returns:
        dict[str, Any]: Updated provider record.
    """
    check_write_rate_limit(request)
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
    await audit_log(request, "provider.update", "provider", name, gateway=get_gateway_name(request))
    return result


@router.delete(
    "/{name}",
    response_model=ProviderDeleteResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def delete_provider(
    name: str,
    request: Request,
    svc: ProviderService = Depends(_get_provider_service),
) -> dict[str, bool]:
    """Delete a provider by name.

    Args:
        name: Provider name.
        request: Incoming HTTP request.
        svc: Injected provider service.

    Returns:
        dict[str, bool]: Deletion status.
    """
    deleted = await asyncio.to_thread(svc.delete, name)
    if deleted:
        logger.info(
            "Provider deleted (provider=%s, actor=%s)",
            name,
            get_actor(request),
        )
        await audit_log(
            request, "provider.delete", "provider", name, gateway=get_gateway_name(request)
        )
    return {"deleted": deleted}
