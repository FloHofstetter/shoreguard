"""REST endpoints for the upstream provider-profile registry.

Provider profiles describe how each provider type wires up credentials,
endpoints, and binaries — the gateway's ``openshell.yaml`` was promoted
to a typed registry in `NVIDIA/OpenShell#1170
<https://github.com/NVIDIA/OpenShell/pull/1170>`_, alongside the
``providers_v2_enabled`` setting that gates the surface.

This router thinly wraps
:class:`~shoreguard.client.provider_profiles.ProviderProfileManager` so
operators can list, inspect, lint, import, and delete profiles from the
ShoreGuard UI without falling back to ``grpcurl``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_client, get_gateway_name
from shoreguard.api.schemas import (
    DeleteProviderProfileResponse,
    ImportProviderProfilesRequest,
    ImportProviderProfilesResponse,
    LintProviderProfilesRequest,
    LintProviderProfilesResponse,
    PaginatedResponse,
    ProviderProfileSchema,
)
from shoreguard.api.validation import check_write_rate_limit
from shoreguard.client import ShoreGuardClient
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


def _client(client: ShoreGuardClient = Depends(get_client)) -> ShoreGuardClient:
    return client


@router.get("", response_model=PaginatedResponse)
async def list_provider_profiles(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    client: ShoreGuardClient = Depends(_client),
) -> dict[str, Any]:
    """List provider profiles registered on the gateway.

    Args:
        limit: Pagination limit.
        offset: Pagination offset.
        client: Injected gRPC client.

    Returns:
        dict[str, Any]: ``{items, total}``. ``total`` is None because the
            upstream RPC returns paginated slices without a total count.
    """
    items = await asyncio.to_thread(client.provider_profiles.list, limit=limit, offset=offset)
    return {"items": items, "total": None}


@router.get("/{profile_id}", response_model=ProviderProfileSchema)
async def get_provider_profile(
    profile_id: str,
    client: ShoreGuardClient = Depends(_client),
) -> dict[str, Any]:
    """Fetch a single profile by ID.

    Args:
        profile_id: Profile ID (e.g. ``"claude"``).
        client: Injected gRPC client.

    Returns:
        dict[str, Any]: Profile dict.
    """
    return await asyncio.to_thread(client.provider_profiles.get, profile_id)


@router.post(
    "/lint",
    response_model=LintProviderProfilesResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def lint_provider_profiles(
    body: LintProviderProfilesRequest,
    request: Request,
    client: ShoreGuardClient = Depends(_client),
) -> dict[str, Any]:
    """Validate a batch of profiles without applying them.

    Args:
        body: Profiles to lint.
        request: Incoming HTTP request.
        client: Injected gRPC client.

    Returns:
        dict[str, Any]: ``{valid, diagnostics}``.
    """
    check_write_rate_limit(request)
    items = [item.model_dump() for item in body.profiles]
    return await asyncio.to_thread(client.provider_profiles.lint, items)


@router.post(
    "/import",
    response_model=ImportProviderProfilesResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def import_provider_profiles(
    body: ImportProviderProfilesRequest,
    request: Request,
    client: ShoreGuardClient = Depends(_client),
) -> dict[str, Any]:
    """Import a batch of profiles after gateway-side validation.

    The gateway returns a partial result on validation failure
    (``imported=False`` with diagnostics) — callers should branch on the
    flag rather than the HTTP status.

    Args:
        body: Profiles to import.
        request: Incoming HTTP request.
        client: Injected gRPC client.

    Returns:
        dict[str, Any]: ``{imported, profiles, diagnostics}``.
    """
    check_write_rate_limit(request)
    items = [item.model_dump() for item in body.profiles]
    result = await asyncio.to_thread(client.provider_profiles.import_, items)
    actor = get_actor(request)
    gw = get_gateway_name(request)
    logger.info(
        "Provider profiles import (count=%d, imported=%s, actor=%s)",
        len(items),
        result["imported"],
        actor,
    )
    await audit_log(
        request,
        "provider_profile.import",
        "provider_profile",
        ",".join(it.get("profile", {}).get("id", "") for it in items)[:200],
        gateway=gw,
        detail={"count": len(items), "imported": result["imported"]},
    )
    return result


@router.delete(
    "/{profile_id}",
    response_model=DeleteProviderProfileResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def delete_provider_profile(
    profile_id: str,
    request: Request,
    client: ShoreGuardClient = Depends(_client),
) -> dict[str, bool]:
    """Delete a custom profile by ID.

    Args:
        profile_id: Profile ID to delete.
        request: Incoming HTTP request.
        client: Injected gRPC client.

    Returns:
        dict[str, bool]: ``{deleted}``.
    """
    check_write_rate_limit(request)
    deleted = await asyncio.to_thread(client.provider_profiles.delete, profile_id)
    if deleted:
        actor = get_actor(request)
        gw = get_gateway_name(request)
        logger.info(
            "Provider profile deleted (profile=%s, actor=%s)",
            profile_id,
            actor,
        )
        await audit_log(
            request,
            "provider_profile.delete",
            "provider_profile",
            profile_id,
            gateway=gw,
        )
    return {"deleted": deleted}
