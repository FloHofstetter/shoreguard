"""Policy pin schemas."""

from __future__ import annotations

from pydantic import BaseModel

# ─── Policy Pinning ──────────────────────────────────────────────────────────


class PolicyPinRequest(BaseModel):
    """Body for pinning a sandbox's policy.

    Attributes:
        reason (str | None): Optional human-readable reason for pinning.
        expires_at (str | None): Optional ISO 8601 expiry timestamp.
    """

    reason: str | None = None
    expires_at: str | None = None


class PolicyPinResponse(BaseModel):
    """Response for a policy pin.

    Attributes:
        gateway_name (str): Gateway the sandbox belongs to.
        sandbox_name (str): Name of the pinned sandbox.
        pinned_version (int): Policy version that is locked.
        pinned_by (str): Actor who set the pin.
        reason (str | None): Optional reason for pinning.
        pinned_at (str): ISO 8601 timestamp when pinned.
        expires_at (str | None): Optional ISO 8601 expiry timestamp.
    """

    gateway_name: str
    sandbox_name: str
    pinned_version: int
    pinned_by: str
    reason: str | None = None
    pinned_at: str
    expires_at: str | None = None
