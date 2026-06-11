"""REST endpoint for the deployment security posture self-check.

A single read-only route returning the structured "am I exposed?"
report built by :mod:`shoreguard.services.security_posture`. Mounted
admin-only under ``/api/security`` — the Security page renders it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from shoreguard.api.deps import get_services
from shoreguard.services.security_posture import collect_posture
from shoreguard.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/posture")
async def get_security_posture() -> dict[str, Any]:
    """Return the security posture report for this deployment (admin only).

    Returns:
        dict[str, Any]: Posture checks, severity summary, and Tailscale
            detection flag.
    """
    return await collect_posture(get_settings(), get_services().registry)
