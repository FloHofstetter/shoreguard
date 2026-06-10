"""Audit log schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# ─── Audit ────────────────────────────────────────────────────────────────────


class AuditEntryResponse(BaseModel):
    """Single audit log entry.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (int | None): Audit entry ID.
        timestamp (str | None): ISO timestamp when the event was recorded.
        actor (str | None): Identifier of the actor who performed the action.
        actor_role (str | None): Role of the actor at the time of the action.
        action (str | None): Action name (e.g. ``sandbox.create``).
        resource_type (str | None): Type of resource the action targeted.
        resource_id (str | None): ID of the targeted resource.
        gateway (str | None): Name of the gateway involved, if any.
        detail (dict[str, Any] | None): Additional structured context.
        client_ip (str | None): Remote client IP address.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    timestamp: str | None = None
    actor: str | None = None
    actor_role: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    gateway: str | None = None
    detail: dict[str, Any] | None = None
    client_ip: str | None = None


class AuditListResponse(BaseModel):
    """Paginated audit log response.

    Attributes:
        entries (list[AuditEntryResponse]): Page of audit entries.
        total (int): Total number of entries matching the query.
    """

    entries: list[AuditEntryResponse]
    total: int
