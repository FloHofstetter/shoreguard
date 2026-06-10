"""Sandbox metadata and boot hook models."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class SandboxMeta(Base):
    """ShoreGuard-side metadata for a sandbox (labels, description).

    Sandboxes live on the OpenShell gateway; this table stores metadata
    that ShoreGuard manages independently.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Name of the gateway the sandbox belongs to.
        sandbox_name: Name of the sandbox (unique per gateway).
        description: Optional free-text description.
        labels_json: Optional JSON-encoded key-value labels.
        created_at: Timestamp when the metadata was first stored.
        updated_at: Timestamp of the last metadata update.
    """

    __tablename__ = "sandbox_meta"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    labels_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class SandboxBootHook(Base):
    """A pre- or post-create boot hook attached to a sandbox.

    Pre-create hooks act as ShoreGuard-side validation gates: their
    commands execute via ``subprocess.run`` inside the ShoreGuard
    process *before* ``CreateSandbox`` reaches the gateway, with a
    whitelisted environment exposing only ``SG_SANDBOX_NAME``,
    ``SG_SANDBOX_IMAGE``, ``SG_SANDBOX_POLICY_ID``, and the hook's
    user-defined ``env`` entries.

    Post-create hooks run *inside* the new sandbox via the existing
    ``ExecSandbox`` RPC once creation succeeds, intended for warm-up
    tasks like package updates or telemetry initialisation.

    The execution surface is deliberately on the ShoreGuard side
    because the upstream gRPC contract has no native hook RPC. Once
    one exists, ``BootHookService`` can detect it and delegate
    without the schema changing.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox this hook attaches to.
        name: Human-readable hook name (unique per sandbox+phase).
        phase: ``pre_create`` or ``post_create``.
        command: Shell command to execute (parsed via shlex).
        workdir: Working directory inside the sandbox (post-create only).
        env_json: JSON-encoded extra environment variables.
        timeout_seconds: Hard wall-clock timeout for the hook.
        order: Sort key within (sandbox, phase).
        enabled: Whether the hook participates in automatic runs.
        continue_on_failure: If true, post-create failures don't abort
            subsequent hooks (pre-create always aborts on failure).
        created_by: Identity of the user who created the hook.
        created_at: Timestamp when the hook was created.
        updated_at: Timestamp of the last update.
        last_run_at: Timestamp of the most recent run.
        last_status: ``success`` / ``failure`` / ``skipped`` / ``None``.
        last_output: Captured stdout+stderr (truncated to 4 KiB).
    """

    __tablename__ = "sandbox_boot_hooks"
    __table_args__ = (
        UniqueConstraint("gateway_name", "sandbox_name", "phase", "name"),
        Index(
            "ix_sandbox_boot_hooks_lookup",
            "gateway_name",
            "sandbox_name",
            "phase",
            "order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    workdir: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    continue_on_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_output: Mapped[str | None] = mapped_column(Text)
