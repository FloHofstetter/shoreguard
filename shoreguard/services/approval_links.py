"""Signed one-tap approve/reject links for approval notifications.

Lets a phone notification (ntfy action button, Telegram inline button)
carry the decision directly: the link encodes gateway, sandbox, chunk,
and decision in an HMAC-signed, short-lived token. Tapping it opens a
minimal confirmation page that casts the vote — no login round-trip on
a phone.

Capability semantics: **anyone holding a link can cast that one vote
until it expires.** That is the explicit trade-off of the opt-in
``SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS`` setting; the notification
channel becomes part of the trust boundary. Tokens are signed with the
same HMAC secret as session cookies and verified with constant-time
comparison.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

_DECISIONS = ("approve", "reject")
# Events that describe a pending human decision and may carry links.
_LINKABLE_EVENTS = ("approval.pending", "approval.escalated")


def _secret() -> bytes:
    """Return the HMAC secret shared with the session-token signer.

    Returns:
        bytes: The active HMAC secret (empty when auth is uninitialised).
    """
    from shoreguard.api.auth.core import state

    return state.hmac_secret


def make_one_tap_token(
    *, gateway: str, sandbox: str, chunk_id: str, decision: str, ttl: int
) -> str:
    """Create a signed one-tap vote token.

    Format: ``<b64url(payload-json)>.<hex-hmac>`` where the payload carries
    gateway, sandbox, chunk id, decision, and an absolute expiry timestamp.

    Args:
        gateway: Gateway name the sandbox lives on.
        sandbox: Sandbox name.
        chunk_id: Draft policy chunk identifier.
        decision: ``approve`` or ``reject``.
        ttl: Validity window in seconds from now.

    Returns:
        str: The signed token.

    Raises:
        ValueError: If *decision* is not ``approve`` or ``reject``.
    """
    if decision not in _DECISIONS:
        raise ValueError(f"Invalid decision: {decision!r}")
    payload = {
        "gw": gateway,
        "sb": sandbox,
        "ch": chunk_id,
        "d": decision,
        "exp": int(time.time()) + ttl,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_one_tap_token(token: str) -> dict[str, Any] | None:
    """Verify a one-tap token and return its payload, or None.

    Args:
        token: The token string from the link.

    Returns:
        dict[str, Any] | None: ``{"gateway", "sandbox", "chunk_id",
            "decision"}`` when the signature is valid and not expired,
            otherwise ``None``.
    """
    secret = _secret()
    if not secret:
        return None
    raw, _, sig = token.rpartition(".")
    if not raw or not sig:
        return None
    expected = hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        if int(payload["exp"]) < int(time.time()):
            return None
        decision = payload["d"]
        if decision not in _DECISIONS:
            return None
        return {
            "gateway": str(payload["gw"]),
            "sandbox": str(payload["sb"]),
            "chunk_id": str(payload["ch"]),
            "decision": decision,
        }
    except ValueError, KeyError, TypeError:
        return None


def enrich_approval_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Attach one-tap links and a page URL to approval event payloads.

    No-op unless ``SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS`` is on, a
    ``SHOREGUARD_PUBLIC_URL`` is configured, the event is a pending-decision
    approval event, and the payload identifies gateway + sandbox. When the
    payload lacks a ``chunk_id``, only the page URL is attached (a chunk-less
    notification can still deep-link to the approvals tab).

    Args:
        event_type: The webhook event type being fired.
        payload: The event payload (not mutated).

    Returns:
        dict[str, Any]: The payload, possibly extended with ``approve_url``,
            ``reject_url``, and ``page_url``.
    """
    from shoreguard.settings import get_settings

    settings = get_settings()
    if not settings.webhooks.one_tap_approvals or event_type not in _LINKABLE_EVENTS:
        return payload
    base = (settings.server.public_url or "").rstrip("/")
    gateway = payload.get("gateway")
    sandbox = payload.get("sandbox")
    if not base or not gateway or not sandbox:
        return payload

    enriched = dict(payload)
    enriched["page_url"] = (
        f"{base}/gateways/{quote(str(gateway))}/sandboxes/{quote(str(sandbox))}/approvals"
    )
    chunk_id = payload.get("chunk_id")
    if chunk_id:
        ttl = settings.webhooks.one_tap_ttl
        for decision in _DECISIONS:
            token = make_one_tap_token(
                gateway=str(gateway),
                sandbox=str(sandbox),
                chunk_id=str(chunk_id),
                decision=decision,
                ttl=ttl,
            )
            enriched[f"{decision}_url"] = f"{base}/approvals/one-tap?token={quote(token)}"
    return enriched
