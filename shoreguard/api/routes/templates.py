"""REST endpoints for listing and reading sandbox templates.

Templates are YAML bundles shipped with ShoreGuard that
pre-configure an image, GPU preference, provider set, policy
presets, and environment for a new sandbox. This module exposes
read-only endpoints — listing and fetching a single template —
so the sandbox wizard can render a picker. Templates are not
editable at runtime; changes go through the packaged YAML files
and ship as part of a ShoreGuard release.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from shoreguard.api.schemas import TemplateDetailResponse, TemplateSummaryResponse
from shoreguard.sandbox_templates import get_template, list_templates

router = APIRouter()


@router.get("", response_model=list[TemplateSummaryResponse])
async def list_sandbox_templates() -> list[dict[str, str]]:
    """List all available sandbox templates.

    Returns:
        list[dict[str, str]]: Template metadata entries.
    """
    return list_templates()


@router.get("/{name}", response_model=TemplateDetailResponse)
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
