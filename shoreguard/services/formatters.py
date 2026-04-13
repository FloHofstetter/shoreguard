"""Channel-specific payload formatters for webhook notifications.

Each supported channel (Slack, Discord, Email, generic JSON)
expects a different payload shape. Rather than branch inside the
delivery pipeline, these formatters take a uniform
``(event, resource)`` input and produce the channel-appropriate
body: Slack Block Kit, Discord embed fields, plain-text email,
or signed generic JSON.

Pure functions with no I/O so the delivery pipeline in
:mod:`shoreguard.services.webhooks` can render and sign a
payload without touching the network, which makes retry-on-send
straightforward and testing trivial.
"""

from __future__ import annotations

import json
from typing import Any

_EVENT_LABELS: dict[str, str] = {
    "approval.pending": "Approval Pending",
    "approval.approved": "Approval Approved",
    "approval.rejected": "Approval Rejected",
    "sandbox.created": "Sandbox Created",
    "sandbox.deleted": "Sandbox Deleted",
    "gateway.registered": "Gateway Registered",
    "gateway.unregistered": "Gateway Unregistered",
    "inference.updated": "Inference Updated",
    "policy.updated": "Policy Updated",
    "webhook.test": "Test Event",
}

_SLACK_COLORS: dict[str, str] = {
    "approval.pending": "warning",
    "approval.approved": "good",
    "approval.rejected": "danger",
    "sandbox.created": "#2196F3",
    "sandbox.deleted": "#9E9E9E",
    "gateway.registered": "good",
    "gateway.unregistered": "warning",
    "inference.updated": "#2196F3",
    "policy.updated": "#2196F3",
    "webhook.test": "#6C757D",
}

_DISCORD_COLORS: dict[str, int] = {
    "approval.pending": 0xFFA500,
    "approval.approved": 0x2ECC71,
    "approval.rejected": 0xE74C3C,
    "sandbox.created": 0x2196F3,
    "sandbox.deleted": 0x9E9E9E,
    "gateway.registered": 0x2ECC71,
    "gateway.unregistered": 0xFFA500,
    "inference.updated": 0x2196F3,
    "policy.updated": 0x2196F3,
    "webhook.test": 0x6C757D,
}


def _event_label(event_type: str) -> str:
    """Return a human-readable label for an event type.

    Args:
        event_type: Machine-readable event type string.

    Returns:
        str: Human-readable label.
    """
    return _EVENT_LABELS.get(event_type, event_type.replace(".", " ").title())


def _payload_fields(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract key-value pairs from a payload for display.

    Args:
        payload: Event data payload.

    Returns:
        list[tuple[str, str]]: List of (label, value) pairs.
    """
    fields = []
    for key in (
        "sandbox",
        "gateway",
        "actor",
        "reason",
        "message",
        "provider",
        "model",
        "image",
        "endpoint",
    ):
        if key in payload:
            fields.append((key.title(), str(payload[key])))
    return fields


def format_generic(event_type: str, payload: dict[str, Any], timestamp: str) -> str:
    """Format a generic webhook payload (JSON envelope with HMAC signing).

    Args:
        event_type: Machine-readable event type.
        payload: Event data payload.
        timestamp: ISO-8601 timestamp string.

    Returns:
        str: JSON-encoded payload string.
    """
    return json.dumps(
        {"event": event_type, "timestamp": timestamp, "data": payload},
        default=str,
    )


def format_slack(event_type: str, payload: dict[str, Any], timestamp: str) -> str:
    """Format a Slack Block Kit message.

    Args:
        event_type: Machine-readable event type.
        payload: Event data payload.
        timestamp: ISO-8601 timestamp string.

    Returns:
        str: JSON-encoded Slack payload.
    """
    label = _event_label(event_type)
    color = _SLACK_COLORS.get(event_type, "#6C757D")
    fields = _payload_fields(payload)
    field_text = "\n".join(f"*{k}:* {v}" for k, v in fields) if fields else ""

    attachment = {
        "color": color,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{label}*\n{field_text}"},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"ShoreGuard | {timestamp}"}],
            },
        ],
    }
    return json.dumps({"attachments": [attachment]})


def format_discord(event_type: str, payload: dict[str, Any], timestamp: str) -> str:
    """Format a Discord embed message.

    Args:
        event_type: Machine-readable event type.
        payload: Event data payload.
        timestamp: ISO-8601 timestamp string.

    Returns:
        str: JSON-encoded Discord payload.
    """
    label = _event_label(event_type)
    color = _DISCORD_COLORS.get(event_type, 0x6C757D)
    fields = [{"name": k, "value": v, "inline": True} for k, v in _payload_fields(payload)]

    embed: dict[str, Any] = {
        "title": label,
        "color": color,
        "timestamp": timestamp,
        "footer": {"text": "ShoreGuard"},
    }
    if fields:
        embed["fields"] = fields

    return json.dumps({"embeds": [embed]})


def format_email_body(event_type: str, payload: dict[str, Any], timestamp: str) -> str:
    """Format a plain-text email body.

    Args:
        event_type: Machine-readable event type.
        payload: Event data payload.
        timestamp: ISO-8601 timestamp string.

    Returns:
        str: Plain-text email body.
    """
    label = _event_label(event_type)
    lines = [f"ShoreGuard — {label}", f"Time: {timestamp}", ""]
    for key, value in _payload_fields(payload):
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


FORMATTERS: dict[str, Any] = {
    "generic": format_generic,
    "slack": format_slack,
    "discord": format_discord,
    "email": format_email_body,
}
