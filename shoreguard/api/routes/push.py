"""REST endpoints for Web Push device subscriptions.

A device (phone PWA, desktop browser) fetches the VAPID public key,
subscribes via the browser push manager, and registers the resulting
endpoint here. The ``webpush`` webhook channel then fans events out to
every registered device. Subscriptions are user-bound: each device
belongs to the user who registered it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from shoreguard.api.deps import get_services
from shoreguard.services.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


class PushKeys(BaseModel):
    """Client encryption keys from ``PushSubscription.toJSON()``.

    Attributes:
        p256dh: Client public key (base64url).
        auth: Client auth secret (base64url).
    """

    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


class PushSubscribeRequest(BaseModel):
    """Request body for registering a device subscription.

    Attributes:
        endpoint: Push-service endpoint URL.
        keys: Client encryption keys.
    """

    endpoint: str = Field(max_length=2048)
    keys: PushKeys


def _actor_email(request: Request) -> str:
    """Return the acting user's email from request state.

    Args:
        request: The incoming HTTP request.

    Returns:
        str: The user email (``no-auth`` in dev-bypass mode).

    Raises:
        HTTPException: 400 when the caller is a service principal —
            push subscriptions are device/user-bound.
    """
    actor = str(getattr(request.state, "user_id", "unknown"))
    if actor.startswith("sp:"):
        raise HTTPException(400, "Push subscriptions are user-bound; use a browser session")
    return actor


@router.get("/public-key")
async def get_public_key() -> dict[str, str]:
    """Return the VAPID public key for ``pushManager.subscribe``.

    Returns:
        dict[str, str]: ``{"public_key": <base64url>}``.
    """
    return {"public_key": get_services().push.public_key()}


@router.get("/subscriptions")
async def list_subscriptions(request: Request) -> list[dict[str, Any]]:
    """List the calling user's registered devices.

    Args:
        request: The incoming HTTP request.

    Returns:
        list[dict[str, Any]]: Subscription records, newest first.
    """
    return await get_services().push.list_for_user(_actor_email(request))


@router.post("/subscriptions", status_code=201)
async def subscribe(body: PushSubscribeRequest, request: Request) -> dict[str, Any]:
    """Register (or refresh) this device's push subscription.

    Args:
        body: The browser subscription (endpoint + keys).
        request: The incoming HTTP request.

    Returns:
        dict[str, Any]: The stored subscription record.
    """
    email = _actor_email(request)
    result = await get_services().push.subscribe(
        user_email=email,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=request.headers.get("user-agent", "")[:512] or None,
    )
    await audit_log(request, "push.subscribe", "push_subscription", str(result["id"]))
    return result


@router.delete("/subscriptions", status_code=204)
async def unsubscribe(request: Request, endpoint: str = Query(max_length=2048)) -> None:
    """Remove this device's push subscription.

    Args:
        request: The incoming HTTP request.
        endpoint: The push-service endpoint URL to remove.

    Raises:
        HTTPException: 404 when no subscription matches the endpoint.
    """
    removed = await get_services().push.unsubscribe(endpoint)
    if not removed:
        raise HTTPException(404, "No subscription with that endpoint")
    await audit_log(request, "push.unsubscribe", "push_subscription", endpoint[:60])


@router.post("/test")
async def send_test(request: Request) -> dict[str, int]:
    """Send a test notification to the calling user's devices.

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, int]: ``sent`` / ``failed`` / ``pruned`` counts.

    Raises:
        HTTPException: 404 when the user has no registered devices.
    """
    import json

    email = _actor_email(request)
    svc = get_services().push
    if not await svc.list_for_user(email):
        raise HTTPException(404, "No devices registered — enable push on this device first")
    payload = json.dumps(
        {"title": "ShoreGuard test", "body": "Push notifications are working.", "url": "/"}
    )
    return await svc.send_payload(payload, only_email=email)


@router.post("/test-approval")
async def send_test_approval(request: Request) -> dict[str, int]:
    """Send a sample *approval* notification to the calling user's devices.

    Mimics the shape of a real ``approval.pending`` push (title, body, and a deep
    link into the approval inbox) so the operator can confirm — by tapping it on
    their phone — that the whole notify -> tap -> ShoreGuard loop works, before
    relying on it overnight. It does not fabricate a real pending approval on a
    gateway, so the framing is an honest setup test.

    Args:
        request: The incoming HTTP request.

    Returns:
        dict[str, int]: ``sent`` / ``failed`` / ``pruned`` counts.

    Raises:
        HTTPException: 404 when the user has no registered devices.
    """
    import json

    email = _actor_email(request)
    svc = get_services().push
    if not await svc.list_for_user(email):
        raise HTTPException(404, "No devices registered — enable push on this device first")
    payload = json.dumps(
        {
            "title": "Approval pending (test)",
            "body": "This is what an agent approval looks like. Tap to open your inbox.",
            "url": "/approvals",
        }
    )
    return await svc.send_payload(payload, only_email=email)
