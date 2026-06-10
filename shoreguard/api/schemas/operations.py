"""Long-running operation schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# ─── Operations ───────────────────────────────────────────────────────────────


class OperationResponse(BaseModel):
    """Single operation record.

    Attributes:
        model_config (ConfigDict): Pydantic config.
        id (str): Operation ID.
        status (str): Current operation status.
        resource_type (str): Type of resource the operation targets.
        progress (int | None): Progress percentage (0–100), if known.
        created_at (str | None): ISO timestamp when the operation was created.
        updated_at (str | None): ISO timestamp of the last update.
        progress_message (str | None): Human-readable progress message.
        result (dict[str, Any] | None): Result payload once the operation finishes.
        error (str | None): Error message if the operation failed.
        error_code (str | None): Machine-readable error code if the operation failed.
        completed_at (str | None): ISO timestamp when the operation completed.
        gateway_name (str | None): Name of the gateway that ran the operation.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    status: str
    resource_type: str
    progress: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    progress_message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    completed_at: str | None = None
    gateway_name: str | None = None


class OperationListResponse(BaseModel):
    """Paginated operation list.

    Attributes:
        operations (list[OperationResponse]): Page of operation records.
        total (int): Total number of operations matching the query.
    """

    operations: list[OperationResponse]
    total: int
