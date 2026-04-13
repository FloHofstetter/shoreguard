"""REST endpoints for the per-sandbox Supply-Chain Viewer.

Routes let operators upload CycloneDX JSON documents (typically
from a CI pipeline), browse components with debounced search and a
severity filter, list vulnerabilities sorted by highest severity
first, fetch the raw payload back, and delete a snapshot.

Upload is the only ingestion path — there is no gateway-pull
alternative. Parsing, storage, and query logic all live in
:class:`~shoreguard.services.sbom.SBOMService`; this module only
handles auth, validation, and response shaping.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_actor, get_gateway_name
from shoreguard.exceptions import InvalidSBOMError
from shoreguard.services.audit import audit_log

if TYPE_CHECKING:
    from shoreguard.services.sbom import SBOMService

logger = logging.getLogger(__name__)

router = APIRouter()

#: Hard cap on a single SBOM upload payload (10 MiB). CycloneDX docs that
#: list every transitive dep of a large container image typically land
#: around 1-3 MiB; 10 MiB leaves comfortable headroom without inviting
#: abusive payloads.
MAX_SBOM_BYTES = 10 * 1024 * 1024


def _get_sbom_service() -> SBOMService:
    """Return the global ``SBOMService`` singleton.

    Returns:
        SBOMService: The active SBOM service instance.

    Raises:
        HTTPException: If the service has not been initialised.
    """
    from shoreguard.services import sbom as sbom_mod

    if sbom_mod.sbom_service is None:
        raise HTTPException(503, "SBOMService not initialised")
    return sbom_mod.sbom_service


@router.post(
    "/{name}/sbom",
    dependencies=[Depends(require_role("admin"))],
    response_model=None,
)
async def upload_sbom(
    name: str,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> JSONResponse:
    """Ingest a CycloneDX JSON SBOM, replacing any prior snapshot.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request (used for raw body + audit context).
        gw: Gateway name from the URL path.

    Returns:
        JSONResponse: ``201`` with the freshly stored snapshot metadata.

    Raises:
        HTTPException: ``413`` if the body exceeds :data:`MAX_SBOM_BYTES`,
            ``400`` if the document is not a valid CycloneDX SBOM.
    """
    body = await request.body()
    if len(body) > MAX_SBOM_BYTES:
        raise HTTPException(413, f"SBOM payload exceeds {MAX_SBOM_BYTES} bytes")
    if not body:
        raise HTTPException(400, "SBOM payload is empty")

    try:
        raw_text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "SBOM payload must be UTF-8 JSON") from exc

    svc = _get_sbom_service()
    actor = get_actor(request)
    try:
        snapshot = svc.ingest(gw, name, raw_text, uploaded_by=actor)
    except InvalidSBOMError as exc:
        raise HTTPException(400, str(exc)) from exc

    await audit_log(
        request,
        "sbom.uploaded",
        "sbom",
        f"{gw}/{name}",
        gateway=gw,
        detail={
            "components": snapshot["component_count"],
            "vulnerabilities": snapshot["vulnerability_count"],
            "max_severity": snapshot["max_severity"],
        },
    )
    return JSONResponse(status_code=201, content=snapshot)


@router.get(
    "/{name}/sbom",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def get_sbom(
    name: str,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Return SBOM snapshot metadata for a sandbox.

    Args:
        name: Sandbox name.
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: Snapshot metadata (no raw payload, no components).

    Raises:
        HTTPException: ``404`` if no snapshot has been uploaded.
    """
    svc = _get_sbom_service()
    snapshot = svc.get_snapshot(gw, name)
    if snapshot is None:
        raise HTTPException(404, "No SBOM uploaded for this sandbox")
    return snapshot


@router.get(
    "/{name}/sbom/components",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def list_sbom_components(
    name: str,
    gw: str = Depends(get_gateway_name),
    search: str = Query("", description="Substring match against name + purl"),
    severity: str = Query(
        "",
        description="Filter by max severity (CRITICAL/HIGH/MEDIUM/LOW/INFO/UNKNOWN/CLEAN)",
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Return a paginated, filtered slice of the components table.

    Args:
        name: Sandbox name.
        gw: Gateway name from the URL path.
        search: Optional case-insensitive substring filter.
        severity: Optional severity filter (or ``CLEAN`` for vuln-free).
        offset: Pagination offset.
        limit: Pagination page size.

    Returns:
        dict[str, Any]: ``{items, total, offset, limit}``.
    """
    svc = _get_sbom_service()
    items, total = svc.search_components(
        gw,
        name,
        search=search or None,
        severity=severity or None,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get(
    "/{name}/sbom/vulnerabilities",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def list_sbom_vulnerabilities(
    name: str,
    gw: str = Depends(get_gateway_name),
) -> dict[str, Any]:
    """Return the structured vulnerability list (highest severity first).

    Args:
        name: Sandbox name.
        gw: Gateway name from the URL path.

    Returns:
        dict[str, Any]: ``{vulnerabilities, count}``.

    Raises:
        HTTPException: ``404`` if no snapshot has been uploaded.
    """
    svc = _get_sbom_service()
    vulns = svc.get_vulnerabilities(gw, name)
    if vulns is None:
        raise HTTPException(404, "No SBOM uploaded for this sandbox")
    return {"vulnerabilities": vulns, "count": len(vulns)}


@router.get(
    "/{name}/sbom/raw",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def get_sbom_raw(
    name: str,
    gw: str = Depends(get_gateway_name),
) -> Response:
    """Return the original CycloneDX JSON document for download.

    Args:
        name: Sandbox name.
        gw: Gateway name from the URL path.

    Returns:
        Response: The raw CycloneDX JSON, served as ``application/vnd.cyclonedx+json``.

    Raises:
        HTTPException: ``404`` if no snapshot has been uploaded.
    """
    svc = _get_sbom_service()
    raw = svc.get_raw_json(gw, name)
    if raw is None:
        raise HTTPException(404, "No SBOM uploaded for this sandbox")
    return Response(
        content=raw,
        media_type="application/vnd.cyclonedx+json",
        headers={"Content-Disposition": f'attachment; filename="{name}.cdx.json"'},
    )


@router.delete(
    "/{name}/sbom",
    dependencies=[Depends(require_role("admin"))],
    response_model=None,
)
async def delete_sbom(
    name: str,
    request: Request,
    gw: str = Depends(get_gateway_name),
) -> Response:
    """Delete a sandbox's SBOM snapshot.

    Args:
        name: Sandbox name.
        request: Incoming HTTP request (used for audit context).
        gw: Gateway name from the URL path.

    Returns:
        Response: Empty ``204`` on success.

    Raises:
        HTTPException: ``404`` if no snapshot was present.
    """
    svc = _get_sbom_service()
    deleted = svc.delete_snapshot(gw, name)
    if not deleted:
        raise HTTPException(404, "No SBOM uploaded for this sandbox")
    await audit_log(
        request,
        "sbom.deleted",
        "sbom",
        f"{gw}/{name}",
        gateway=gw,
    )
    return Response(status_code=204)
