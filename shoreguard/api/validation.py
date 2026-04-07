"""Shared validation helpers for API routes."""

from __future__ import annotations

import re

from fastapi import HTTPException

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
