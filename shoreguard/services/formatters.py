"""Channel-specific payload formatters for webhook notifications.

Each supported channel (Slack, Discord, Email, ntfy, generic JSON)
expects a different payload shape. Rather than branch inside the
delivery pipeline, these formatters take a uniform
``(event, resource)`` input and produce the channel-appropriate
body: Slack Block Kit, Discord embed fields, plain-text email,
an ntfy JSON publish, or signed generic JSON.

Pure functions with no I/O so the delivery pipeline in
:mod:`shoreguard.services.webhooks` can render and sign a
payload without touching the network, which makes retry-on-send
straightforward and testing trivial.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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


# ntfy priorities: 5 = urgent (bypasses do-not-disturb on most phones),
# 4 = high, 3 = default. Approvals are the events a solo operator wants
# their phone to buzz for.
_NTFY_PRIORITIES: dict[str, int] = {
    "approval.pending": 4,
    "approval.escalated": 5,
    "approval.rejected": 4,
}

# ntfy tags: leading entries that are emoji shortcodes render as emoji.
_NTFY_TAGS: dict[str, str] = {
    "approval.pending": "hourglass_flowing_sand",
    "approval.approved": "white_check_mark",
    "approval.rejected": "x",
    "approval.escalated": "rotating_light",
    "sandbox.created": "package",
    "sandbox.deleted": "wastebasket",
    "gateway.registered": "satellite",
    "gateway.unregistered": "satellite",
    "policy.updated": "shield",
    "webhook.test": "bell",
}


def format_ntfy(event_type: str, payload: dict[str, Any], timestamp: str) -> str:
    """Format an ntfy JSON publish message (topic injected at delivery time).

    The ``topic`` field is left empty here — it is parsed from the webhook's
    topic URL by :func:`prepare_ntfy_request` so this formatter stays a pure
    function of the event.

    Args:
        event_type: Machine-readable event type.
        payload: Event data payload.
        timestamp: ISO-8601 timestamp string.

    Returns:
        str: JSON-encoded ntfy publish body.
    """
    label = _event_label(event_type)
    lines = [f"{k}: {v}" for k, v in _payload_fields(payload)]
    lines.append(f"at {timestamp}")
    message: dict[str, Any] = {
        "topic": "",
        "title": f"ShoreGuard — {label}",
        "message": "\n".join(lines),
        "priority": _NTFY_PRIORITIES.get(event_type, 3),
    }
    tag = _NTFY_TAGS.get(event_type)
    if tag:
        message["tags"] = [tag]
    return json.dumps(message)


def prepare_ntfy_request(url: str, body: str) -> tuple[str, str]:
    """Split an ntfy topic URL into the server root and a body with topic set.

    ntfy's JSON publish endpoint is the server *root* with the topic inside
    the body, but operators naturally enter the topic URL they subscribe to
    (e.g. ``https://ntfy.sh/shoreguard``). The last path segment is the topic.

    Args:
        url: The webhook's topic URL.
        body: JSON body produced by :func:`format_ntfy`.

    Returns:
        tuple[str, str]: ``(post_url, body)`` — the server root to POST to
            and the body with the ``topic`` field filled in.
    """
    parsed = urlsplit(url)
    path = parsed.path.strip("/")
    topic = path.split("/")[-1] if path else ""
    root = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    data = json.loads(body)
    data["topic"] = topic
    return root, json.dumps(data)


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
    "ntfy": format_ntfy,
}
