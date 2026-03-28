"""REST endpoint for polling long-running operations."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from shoreguard.services.operations import operation_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{operation_id}")
async def get_operation(operation_id: str) -> dict[str, Any]:
    """Get the current status of a long-running operation."""
    op = operation_store.get(operation_id)
    if op is None:
        logger.debug("Operation not found: %s", operation_id)
        raise HTTPException(status_code=404, detail="Operation not found")
    return operation_store.to_dict(op)
