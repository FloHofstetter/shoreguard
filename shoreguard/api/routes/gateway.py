"""REST endpoints for gateway registration, management, and diagnostics."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
from typing import TYPE_CHECKING, Any

import grpc
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from shoreguard.api.auth import require_role
from shoreguard.api.deps import _get_gateway_service, get_actor
from shoreguard.config import ENDPOINT_RE, VALID_GATEWAY_NAME_RE, is_private_ip
from shoreguard.exceptions import NotFoundError, friendly_grpc_error
from shoreguard.services.audit import audit_log
from shoreguard.services.operations import operation_store
from shoreguard.services.webhooks import fire_webhook

if TYPE_CHECKING:
    from shoreguard.services.local_gateway import LocalGatewayManager

logger = logging.getLogger(__name__)


_VALID_NAME_RE = VALID_GATEWAY_NAME_RE
_MAX_CERT_BYTES = 65_536  # 64 KB — real certs are typically < 10 KB
_MAX_METADATA_JSON_BYTES = 16_384  # 16 KB
_MAX_DESCRIPTION_LEN = 1000
_MAX_LABELS = 20
_MAX_LABEL_VALUE_LEN = 253
_LABEL_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")

router = APIRouter()

_background_tasks: set[asyncio.Task] = set()

_ENDPOINT_RE = ENDPOINT_RE


def _validate_endpoint_format(endpoint: str) -> None:
    """Validate that endpoint is host:port and not pointing at private networks.

    Args:
        endpoint: Endpoint string in host:port format.

    Raises:
        ValueError: If format is invalid, port is out of range, or address is private.
    """
    if not _ENDPOINT_RE.match(endpoint):
        raise ValueError("endpoint must be in host:port format (e.g. '10.0.0.5:8443')")
    host, port_str = endpoint.rsplit(":", 1)
    port = int(port_str)
    if port < 1 or port > 65535:
        raise ValueError("endpoint port must be between 1 and 65535")
    if is_private_ip(host):
        raise ValueError(
            "endpoint must not point to a private/loopback address; use a routable IP or hostname"
        )


def _get_local_manager() -> LocalGatewayManager | None:
    """Return the local gateway manager if running in local mode.

    Returns:
        LocalGatewayManager | None: Manager instance or None if not in local mode.
    """
    if not os.environ.get("SHOREGUARD_LOCAL_MODE"):
        return None
    from shoreguard.services.local_gateway import local_gateway_manager

    return local_gateway_manager


# ─── Request / Response models ─────────────────────────────────────────────


class RegisterGatewayRequest(BaseModel):
    """Request body for registering a remote gateway.

    Attributes:
        name: Gateway name.
        endpoint: Gateway endpoint in host:port format.
        scheme: Connection scheme (http or https).
        auth_mode: Authentication mode (mtls, api_key, none, insecure).
        ca_cert: Base64-encoded CA certificate.
        client_cert: Base64-encoded client certificate.
        client_key: Base64-encoded client private key.
        metadata: Optional metadata dict for the gateway.
        description: Optional human-readable description.
        labels: Optional key-value labels for the gateway.
    """

    name: str
    endpoint: str
    scheme: str = "https"
    auth_mode: str | None = "mtls"
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    metadata: dict[str, Any] | None = None
    description: str | None = None
    labels: dict[str, str] | None = None

    @field_validator("scheme")
    @classmethod
    def validate_scheme(cls, v: str) -> str:  # noqa: D102
        if v not in ("http", "https"):
            raise ValueError("scheme must be 'http' or 'https'")
        return v

    @field_validator("auth_mode")
    @classmethod
    def validate_auth_mode(cls, v: str | None) -> str | None:  # noqa: D102
        allowed = ("mtls", "api_key", "none", "insecure")
        if v is not None and v not in allowed:
            raise ValueError(f"auth_mode must be one of {allowed!r} or null")
        return v

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:  # noqa: D102
        if not v or not v.strip():
            raise ValueError("endpoint must not be empty")
        v = v.strip()
        _validate_endpoint_format(v)
        return v


_REMOTE_HOST_RE = re.compile(r"^[a-zA-Z0-9._-]{1,253}$")


class CreateGatewayRequest(BaseModel):
    """Request body for creating a local gateway (local mode only).

    Attributes:
        name: Gateway name.
        port: Optional port number for the gateway.
        remote_host: Optional remote hostname or IP.
        gpu: Whether to enable GPU access.
    """

    name: str = "openshell"
    port: int | None = None
    remote_host: str | None = None
    gpu: bool = False

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int | None) -> int | None:  # noqa: D102
        if v is not None and (v < 1 or v > 65535):
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("remote_host")
    @classmethod
    def validate_remote_host(cls, v: str | None) -> str | None:  # noqa: D102
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if not _REMOTE_HOST_RE.match(v):
                raise ValueError(
                    "remote_host must be a valid hostname or IP "
                    "(alphanumeric, dots, hyphens, max 253 chars)"
                )
        return v


class UpdateGatewayMetadataRequest(BaseModel):
    """Request body for updating gateway description and/or labels.

    Attributes:
        description: New description (or null to clear).
        labels: New key-value labels (or null to clear).
    """

    description: str | None = None
    labels: dict[str, str] | None = None


def _validate_description(description: str | None) -> None:
    """Validate a gateway description.

    Args:
        description: Description string to validate.

    Raises:
        HTTPException: If the description exceeds the maximum length.
    """
    if description is not None and len(description) > _MAX_DESCRIPTION_LEN:
        raise HTTPException(400, f"description exceeds maximum length of {_MAX_DESCRIPTION_LEN}")


def _validate_labels(labels: dict[str, str] | None) -> None:
    """Validate gateway labels.

    Args:
        labels: Label dict to validate.

    Raises:
        HTTPException: If any label key or value is invalid, or count exceeds limit.
    """
    if labels is None:
        return
    if len(labels) > _MAX_LABELS:
        raise HTTPException(400, f"Too many labels (max {_MAX_LABELS})")
    for key, value in labels.items():
        if not _LABEL_KEY_RE.match(key):
            raise HTTPException(
                400,
                f"Invalid label key '{key}': must match [a-zA-Z0-9][a-zA-Z0-9._-]* (max 63 chars)",
            )
        if len(value) > _MAX_LABEL_VALUE_LEN:
            raise HTTPException(
                400,
                f"Label value for '{key}' exceeds maximum length of {_MAX_LABEL_VALUE_LEN}",
            )


# ─── Gateway queries ──────────────────────────────────────────────────────


@router.get("/list")
async def gateway_list(
    label: list[str] | None = Query(None),
) -> list[dict[str, Any]]:
    """List all registered gateways with metadata and status.

    Args:
        label: Optional label filters in ``key:value`` format. Multiple
            labels are AND-combined.

    Returns:
        list[dict[str, Any]]: Gateway records.

    Raises:
        HTTPException: If a label filter has invalid format.
    """
    labels_filter: dict[str, str] | None = None
    if label:
        labels_filter = {}
        for item in label:
            if ":" not in item or item.startswith(":"):
                raise HTTPException(400, f"Invalid label filter '{item}': expected key:value")
            key, value = item.split(":", 1)
            labels_filter[key] = value
    svc = _get_gateway_service()
    return await asyncio.to_thread(svc.list_all, labels_filter=labels_filter)


@router.get("/{name}/info")
async def gateway_info(name: str) -> dict[str, Any]:
    """Return gateway configuration and connection status.

    Args:
        name: Gateway name.

    Returns:
        dict[str, Any]: Gateway info.
    """
    _validate_name_param(name)
    return await asyncio.to_thread(_get_gateway_service().get_info, name)


@router.get("/{name}/config")
async def gateway_config(name: str) -> dict[str, Any]:
    """Get gateway configuration (settings and revision).

    Args:
        name: Gateway name.

    Returns:
        dict[str, Any]: Gateway configuration.
    """
    _validate_name_param(name)
    return await asyncio.to_thread(_get_gateway_service().get_config, name)


# ─── Registration (v0.3) ──────────────────────────────────────────────────


@router.post("/register", status_code=201, dependencies=[Depends(require_role("admin"))])
async def gateway_register(body: RegisterGatewayRequest, request: Request) -> dict[str, Any]:
    """Register a remote gateway.

    Args:
        body: Registration payload with endpoint and auth details.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Registered gateway record.

    Raises:
        HTTPException: If name is invalid, certs are malformed, metadata too large,
            or a gateway with the same name already exists.
    """
    if not _VALID_NAME_RE.match(body.name):
        raise HTTPException(400, "Invalid gateway name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")

    logger.info(
        "Gateway registration request: name=%s endpoint=%s auth_mode=%s actor=%s",
        body.name,
        body.endpoint,
        body.auth_mode,
        get_actor(request),
    )

    try:
        ca_cert = base64.b64decode(body.ca_cert) if body.ca_cert else None
        client_cert = base64.b64decode(body.client_cert) if body.client_cert else None
        client_key = base64.b64decode(body.client_key) if body.client_key else None
    except binascii.Error as e:
        raise HTTPException(400, f"Invalid base64 in certificate field: {e}") from e

    cert_fields = [("ca_cert", ca_cert), ("client_cert", client_cert), ("client_key", client_key)]
    for label, blob in cert_fields:
        if blob is not None and len(blob) > _MAX_CERT_BYTES:
            raise HTTPException(400, f"{label} exceeds maximum size of {_MAX_CERT_BYTES} bytes")

    _validate_description(body.description)
    _validate_labels(body.labels)

    if body.metadata is not None:
        import json as _json

        try:
            metadata_size = len(_json.dumps(body.metadata))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, f"metadata is not JSON-serializable: {e}") from e
        if metadata_size > _MAX_METADATA_JSON_BYTES:
            raise HTTPException(
                400,
                f"metadata exceeds maximum size of {_MAX_METADATA_JSON_BYTES} bytes",
            )

    try:
        result = await asyncio.to_thread(
            _get_gateway_service().register,
            name=body.name,
            endpoint=body.endpoint,
            scheme=body.scheme,
            auth_mode=body.auth_mode,
            ca_cert=ca_cert,
            client_cert=client_cert,
            client_key=client_key,
            metadata=body.metadata,
            description=body.description,
            labels=body.labels,
        )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e

    await audit_log(
        request,
        "gateway.register",
        "gateway",
        body.name,
        detail={
            "endpoint": body.endpoint,
            "auth_mode": body.auth_mode,
            "description": body.description,
            "labels": body.labels,
        },
    )
    await fire_webhook(
        "gateway.registered",
        {
            "gateway": body.name,
            "endpoint": body.endpoint,
            "actor": get_actor(request),
        },
    )
    return result


def _validate_name_param(name: str) -> None:
    """Validate a gateway name path parameter.

    Args:
        name: Gateway name to validate.

    Raises:
        HTTPException: If the name does not match the allowed pattern.
    """
    if not _VALID_NAME_RE.match(name):
        raise HTTPException(
            400, "Invalid gateway name: must match [a-zA-Z0-9][a-zA-Z0-9._-]* (max 253)"
        )


@router.delete("/{name}", dependencies=[Depends(require_role("admin"))])
async def gateway_unregister(name: str, request: Request) -> dict[str, Any]:
    """Unregister a gateway.

    Args:
        name: Gateway name.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Confirmation with gateway name.

    Raises:
        HTTPException: If the gateway is not found.
    """
    _validate_name_param(name)
    removed = await asyncio.to_thread(_get_gateway_service().unregister, name)
    if not removed:
        logger.warning("Unregister failed: gateway '%s' not found", name)
        raise HTTPException(404, f"Gateway '{name}' not found")
    logger.info("Gateway unregistered (gateway=%s, actor=%s)", name, get_actor(request))
    await audit_log(request, "gateway.unregister", "gateway", name)
    await fire_webhook(
        "gateway.unregistered",
        {"gateway": name, "actor": get_actor(request)},
    )
    return {"success": True, "name": name}


@router.patch("/{name}", dependencies=[Depends(require_role("admin"))])
async def gateway_update_metadata(
    name: str, body: UpdateGatewayMetadataRequest, request: Request
) -> dict[str, Any]:
    """Update gateway description and/or labels.

    Args:
        name: Gateway name.
        body: Fields to update.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Updated gateway record.

    Raises:
        HTTPException: If the gateway is not found or validation fails.
    """
    _validate_name_param(name)

    provided = body.model_fields_set
    if not provided:
        raise HTTPException(400, "No fields to update")

    if "description" in provided:
        _validate_description(body.description)
    if "labels" in provided:
        _validate_labels(body.labels)

    kwargs: dict[str, Any] = {}
    if "description" in provided:
        kwargs["description"] = body.description
    if "labels" in provided:
        kwargs["labels"] = body.labels

    try:
        result = await asyncio.to_thread(
            _get_gateway_service().update_gateway_metadata,
            name,
            **kwargs,
        )
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e

    await audit_log(
        request,
        "gateway.update_metadata",
        "gateway",
        name,
        detail={"description": body.description, "labels": body.labels},
    )
    return result


@router.post("/{name}/test-connection", dependencies=[Depends(require_role("admin"))])
async def gateway_test_connection(name: str, request: Request) -> dict[str, Any]:
    """Explicitly test connectivity to a registered gateway.

    Args:
        name: Gateway name.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Connection test result.

    Raises:
        HTTPException: If the gateway is not found (404).
    """
    _validate_name_param(name)
    try:
        result = await asyncio.to_thread(_get_gateway_service().test_connection, name)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    if result.get("success"):
        logger.info("Connection test passed for gateway '%s'", name)
    else:
        logger.warning("Connection test failed for gateway '%s': %s", name, result.get("error"))
    return result


# ─── Actions on any gateway ───────────────────────────────────────────────


# ─── Local mode routes (SHOREGUARD_LOCAL_MODE=1) ─────────────────────────


@router.get("/diagnostics", dependencies=[Depends(require_role("operator"))])
async def gateway_diagnostics() -> dict[str, Any]:
    """Check Docker availability, daemon status, and permissions (local mode).

    Returns:
        dict[str, Any]: Diagnostic results.

    Raises:
        HTTPException: If not running in local mode.
    """
    mgr = _get_local_manager()
    if mgr is None:
        raise HTTPException(404, "Diagnostics only available in local mode")
    return await asyncio.to_thread(mgr.diagnostics)


@router.post("/{name}/start", dependencies=[Depends(require_role("admin"))])
async def gateway_start_named(name: str, request: Request) -> dict[str, Any]:
    """Start a specific gateway by name (local mode).

    Args:
        name: Gateway name.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Start result.

    Raises:
        HTTPException: If not running in local mode.
    """
    _validate_name_param(name)
    mgr = _get_local_manager()
    if mgr is None:
        raise HTTPException(404, "Local lifecycle only available in local mode")
    logger.info("Gateway start requested (gateway=%s, actor=%s)", name, get_actor(request))
    result = await asyncio.to_thread(mgr.start, name)
    await audit_log(request, "gateway.start", "gateway", name)
    return result


@router.post("/{name}/stop", dependencies=[Depends(require_role("admin"))])
async def gateway_stop_named(name: str, request: Request) -> dict[str, Any]:
    """Stop a specific gateway by name (local mode).

    Args:
        name: Gateway name.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Stop result.

    Raises:
        HTTPException: If not running in local mode.
    """
    _validate_name_param(name)
    mgr = _get_local_manager()
    if mgr is None:
        raise HTTPException(404, "Local lifecycle only available in local mode")
    logger.info("Gateway stop requested (gateway=%s, actor=%s)", name, get_actor(request))
    result = await asyncio.to_thread(mgr.stop, name)
    await audit_log(request, "gateway.stop", "gateway", name)
    return result


@router.post("/{name}/restart", dependencies=[Depends(require_role("admin"))])
async def gateway_restart_named(name: str, request: Request) -> dict[str, Any]:
    """Restart a specific gateway by name (local mode).

    Args:
        name: Gateway name.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Restart result.

    Raises:
        HTTPException: If not running in local mode.
    """
    _validate_name_param(name)
    mgr = _get_local_manager()
    if mgr is None:
        raise HTTPException(404, "Local lifecycle only available in local mode")
    logger.info("Gateway restart requested (gateway=%s, actor=%s)", name, get_actor(request))
    result = await asyncio.to_thread(mgr.restart, name)
    await audit_log(request, "gateway.restart", "gateway", name)
    return result


@router.post("/{name}/destroy", dependencies=[Depends(require_role("admin"))])
async def gateway_destroy(name: str, request: Request, force: bool = False) -> dict[str, Any]:
    """Destroy a gateway and remove its configuration (local mode).

    Args:
        name: Gateway name.
        request: Incoming HTTP request.
        force: Whether to force-destroy even if running.

    Returns:
        dict[str, Any]: Destruction result.

    Raises:
        HTTPException: If not running in local mode.
    """
    _validate_name_param(name)
    mgr = _get_local_manager()
    if mgr is None:
        raise HTTPException(404, "Local lifecycle only available in local mode")
    logger.info(
        "Gateway destroy requested (gateway=%s, force=%s, actor=%s)",
        name,
        force,
        get_actor(request),
    )
    result = await asyncio.to_thread(mgr.destroy, name, force=force)
    await audit_log(request, "gateway.destroy", "gateway", name, detail={"force": force})
    return result


@router.post("/create", status_code=202, dependencies=[Depends(require_role("admin"))])
async def gateway_create(body: CreateGatewayRequest, request: Request) -> dict[str, Any]:
    """Create a new local gateway. Returns 202 with operation ID (local mode).

    Args:
        body: Gateway creation payload.
        request: Incoming HTTP request.

    Returns:
        dict[str, Any]: Operation tracking object with id and status.

    Raises:
        HTTPException: If not in local mode, name is invalid, or creation is
            already in progress.
    """
    mgr = _get_local_manager()
    if mgr is None:
        raise HTTPException(404, "Local gateway creation only available in local mode")

    if not _VALID_NAME_RE.match(body.name):
        raise HTTPException(400, "Invalid gateway name: must match [a-zA-Z0-9][a-zA-Z0-9._-]*")
    op = operation_store.create_if_not_running("gateway", body.name)
    if op is None:
        raise HTTPException(409, f"Gateway '{body.name}' creation already in progress")

    actor = get_actor(request)
    _audit_actor = actor
    _audit_role = getattr(request.state, "role", "unknown")
    _audit_ip = request.client.host if request.client else None

    async def _run() -> None:
        """Execute gateway creation in the background."""
        logger.info("Starting gateway creation: '%s' (op=%s, actor=%s)", body.name, op.id, actor)
        try:
            result = await asyncio.to_thread(
                mgr.create,
                name=body.name,
                port=body.port,
                remote_host=body.remote_host,
                gpu=body.gpu,
            )
            if result.get("success") is False:
                operation_store.fail(op.id, result.get("error", "Gateway creation failed"))
            else:
                logger.info("Gateway creation completed: '%s' (op=%s)", body.name, op.id)
                operation_store.complete(op.id, result)
                from shoreguard.services.audit import audit_service

                if audit_service:
                    await asyncio.to_thread(
                        audit_service.log,
                        actor=_audit_actor,
                        actor_role=_audit_role,
                        action="gateway.create",
                        resource_type="gateway",
                        resource_id=body.name,
                        gateway=body.name,
                        client_ip=_audit_ip,
                    )
        except asyncio.CancelledError:
            logger.warning("Gateway creation cancelled for '%s'", body.name)
            operation_store.fail(op.id, "Operation was cancelled")
        except (grpc.RpcError, OSError, TimeoutError, RuntimeError) as e:
            logger.error("Gateway creation failed for '%s': %s", body.name, e, exc_info=True)
            msg = (
                friendly_grpc_error(e)
                if isinstance(e, grpc.RpcError)
                else "Gateway creation failed unexpectedly"
            )
            try:
                operation_store.fail(op.id, msg)
            except Exception:
                logger.exception("Failed to record operation failure for %s", op.id)
        except Exception:
            logger.exception("Gateway creation failed unexpectedly for '%s'", body.name)
            try:
                operation_store.fail(op.id, "Unexpected internal error")
            except Exception:
                logger.exception("Failed to record operation failure for %s", op.id)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"operation_id": op.id, "status": "running", "resource_type": "gateway"}
