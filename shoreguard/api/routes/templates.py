"""REST endpoints for sandbox templates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from shoreguard.sandbox_templates import get_template, list_templates

router = APIRouter()


@router.get("")
async def list_sandbox_templates() -> list[dict[str, str]]:
    """List all available sandbox templates.

    Returns:
        list[dict[str, str]]: Template metadata entries.
    """
    return list_templates()


@router.get("/{name}")
async def get_sandbox_template(name: str) -> dict[str, Any]:
    """Get a sandbox template by name.

    Args:
        name: Template identifier.

    Returns:
        dict[str, Any]: Full template including sandbox configuration.

    Raises:
        HTTPException: If template is not found.
    """
    template = get_template(name)
    if template is None:
        raise HTTPException(404, f"Template '{name}' not found")
    return template
