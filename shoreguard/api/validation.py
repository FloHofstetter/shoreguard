"""Shared validation helpers for API routes."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from shoreguard.config import is_private_ip
from shoreguard.exceptions import ValidationError as DomainValidationError
from shoreguard.settings import get_settings

LABEL_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")


def validate_description(description: str | None) -> None:
    """Validate a resource description.

    Args:
        description: Description string to validate.

    Raises:
        HTTPException: If the description exceeds the maximum length.
    """
    max_len = get_settings().limits.max_description_len
    if description is not None and len(description) > max_len:
        raise HTTPException(400, f"description exceeds maximum length of {max_len}")


def validate_labels(labels: dict[str, str] | None) -> None:
    """Validate resource labels.

    Args:
        labels: Label dict to validate.

    Raises:
        HTTPException: If any label key or value is invalid, or count exceeds limit.
    """
    if labels is None:
        return
    limits = get_settings().limits
    if len(labels) > limits.max_labels:
        raise HTTPException(400, f"Too many labels (max {limits.max_labels})")
    for key, value in labels.items():
        if not LABEL_KEY_RE.match(key):
            raise HTTPException(
                400,
                f"Invalid label key '{key}': must match [a-zA-Z0-9][a-zA-Z0-9._-]* (max 63 chars)",
            )
        if len(value) > limits.max_label_value_len:
            raise HTTPException(
                400,
                f"Label value for '{key}' exceeds maximum length of {limits.max_label_value_len}",
            )


def validate_webhook_url(url: str) -> str:
    """Validate a webhook URL scheme and reject private/internal targets.

    Args:
        url: The URL to validate.

    Returns:
        str: The cleaned (stripped) URL.

    Raises:
        DomainValidationError: If the URL is invalid or points to a private address.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DomainValidationError(f"URL scheme must be http or https, got '{parsed.scheme}'")
    if not parsed.hostname:
        raise DomainValidationError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise DomainValidationError("URL must not contain credentials")
    if is_private_ip(parsed.hostname) and not get_settings().server.local_mode:
        raise DomainValidationError("URL must not point to a private/loopback address")
    return url


def validate_smtp_host(host: str) -> None:
    """Validate an SMTP host is not a private/internal address.

    Args:
        host: The SMTP hostname to validate.

    Raises:
        DomainValidationError: If the host resolves to a private address.
    """
    if is_private_ip(host) and not get_settings().server.local_mode:
        raise DomainValidationError("SMTP host must not point to a private/loopback address")


def check_write_rate_limit(request: Request) -> None:
    """Check per-user write rate limit; raise 429 if exceeded.

    Uses ``user_id`` from request state for authenticated endpoints,
    falls back to client IP.

    Args:
        request: The incoming HTTP request.

    Raises:
        HTTPException: 429 if the rate limit is exceeded.
    """
    from shoreguard.api.ratelimit import get_write_limiter

    limiter = get_write_limiter()
    key = getattr(request.state, "user_id", None) or (
        request.client.host if request.client else "unknown"
    )
    blocked, retry_after = limiter.is_limited(str(key))
    if blocked:
        raise HTTPException(
            429,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    limiter.record(str(key))
