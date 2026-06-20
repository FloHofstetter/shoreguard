"""Sandbox metadata and boot hook models."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
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


class SandboxBudget(Base):
    """Inference-request budget for one sandbox.

    Phase-1 spend guardrail: the metering task counts inference-proxy log
    lines per sandbox; when the count in the configured window reaches the
    limit, the budget's action fires (notify webhook, or detach the
    sandbox's providers — reversible via the kill-switch resume path).

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name the sandbox lives on.
        sandbox: Sandbox name (unique per gateway).
        limit_requests: Inference request ceiling for the window.
        limit_usd: Optional estimated-dollar ceiling for the window; when
            set it takes precedence over ``limit_requests`` (the budget is
            evaluated against the estimated cost from the pricing overlay).
        window: Budget window — ``daily``, ``weekly``, ``monthly``, ``total``.
        action: What happens at the limit — ``notify`` or ``detach``.
        notified_key: Window key of the last notification (anti-spam).
        created_at: When the budget was created.
        updated_at: When the budget was last changed.
    """

    __tablename__ = "sandbox_budgets"
    __table_args__ = (UniqueConstraint("gateway", "sandbox", name="uq_sandbox_budget"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox: Mapped[str] = mapped_column(String(253), nullable=False)
    limit_requests: Mapped[int] = mapped_column(Integer, nullable=False)
    limit_usd: Mapped[float | None] = mapped_column(Float)
    window: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="notify")
    notified_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SandboxUsage(Base):
    """Per-day inference request counter for one sandbox.

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name.
        sandbox: Sandbox name.
        day: UTC day in ``YYYY-MM-DD`` form.
        requests: Inference requests counted on that day.
    """

    __tablename__ = "sandbox_usage"
    __table_args__ = (
        UniqueConstraint("gateway", "sandbox", "day", name="uq_sandbox_usage_day"),
        Index("ix_sandbox_usage_day", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox: Mapped[str] = mapped_column(String(253), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DenialSample(Base):
    """A persisted L7 denial sample for policy-simulation replay.

    The gateway has no ``GetDenialSummary`` RPC — denial summaries only flow
    inbound via ``SubmitPolicyAnalysis`` and the live cache is in-memory and
    volatile. This table durably records them so the policy simulator can
    replay them against a candidate policy after a restart. Upserted on
    ``(gateway, sandbox, binary, host, port)``; pruned by retention.

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name.
        sandbox: Sandbox name.
        binary: Denied binary path.
        host: Target host of the denied request.
        port: Target port of the denied request.
        l7_samples_json: JSON list of ``{method, path}`` observed requests.
        deny_reason: Why the request was denied.
        count: Observed denial count.
        created_at: When this sample was last recorded.
    """

    __tablename__ = "denial_samples"
    __table_args__ = (
        UniqueConstraint("gateway", "sandbox", "binary", "host", "port", name="uq_denial_sample"),
        Index("ix_denial_samples_sb", "gateway", "sandbox"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox: Mapped[str] = mapped_column(String(253), nullable=False)
    binary: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    host: Mapped[str] = mapped_column(String(253), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    l7_samples_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    deny_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SandboxRateLimit(Base):
    """Per-sandbox inference request-rate ceiling for the rate governor.

    The governor evaluates metered request counts against ``max_requests``
    per a tumbling ``window_seconds`` window; exceeding it trips a reversible
    soft-pause (see :class:`RatePauseEntry`). The window state lives on this
    row so no extra cursor table is needed; it resets when the limit is
    reconfigured or after an auto-resume.

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name the sandbox lives on.
        sandbox: Sandbox name (unique per gateway).
        max_requests: Inference request ceiling within the window.
        window_seconds: Tumbling window length in seconds.
        enabled: Whether the governor evaluates this limit.
        window_started_at: Start of the current tumbling window, or ``None``.
        window_count_start: Cumulative metered count at the window start.
        created_at: When the limit was created.
        updated_at: When the limit was last changed.
    """

    __tablename__ = "sandbox_rate_limits"
    __table_args__ = (UniqueConstraint("gateway", "sandbox", name="uq_sandbox_rate_limit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox: Mapped[str] = mapped_column(String(253), nullable=False)
    max_requests: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    window_started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    window_count_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RatePauseEntry(Base):
    """A reversible soft-pause engaged by the rate governor.

    Distinct from :class:`KillSwitchEntry` on purpose: the kill switch raises
    on collision and its resume re-attaches everything, so the governor owns
    its own table, skips any sandbox already kill-switched, and persists only
    the providers it itself detached so auto-resume re-attaches exactly that.

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name the sandbox lives on.
        sandbox: Sandbox name (unique per gateway).
        providers_json: JSON list of the providers the governor detached.
        paused_at: When the soft-pause was engaged.
        resume_after: When the cooldown elapses and auto-resume may re-attach.
        reason: Why it paused (e.g. ``rate_governor``).
    """

    __tablename__ = "rate_pause_entries"
    __table_args__ = (UniqueConstraint("gateway", "sandbox", name="uq_rate_pause"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    sandbox: Mapped[str] = mapped_column(String(253), nullable=False)
    providers_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    paused_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resume_after: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="rate_governor")


class GatewayInventorySnapshot(Base):
    """A point-in-time snapshot of a gateway's sandboxes and attachments.

    Captured on each successful health probe so a gateway/Docker restart
    that reaps sandboxes can be diffed (pre-down vs post-recovery). Pure
    forensic history — append-only, pruned by retention; never reversible
    state, so it does NOT reuse the kill-switch table.

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name.
        captured_at: When the snapshot was taken.
        sandboxes_json: JSON map ``{sandbox_name: [provider names sorted]}``.
        sandbox_count: Number of sandboxes in the snapshot.
    """

    __tablename__ = "gateway_inventory_snapshots"
    __table_args__ = (Index("ix_gateway_inventory_gw_time", "gateway", "captured_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False)
    captured_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sandboxes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sandbox_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GatewayReapRecord(Base):
    """A record of sandboxes/attachments lost across a gateway restart.

    Written when an ``unreachable → recovered`` transition's inventory diff
    is non-empty. The durable forensic residue of the reconciler: an
    append-only log, never reversible state.

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name.
        detected_at: When the reap was detected (on recovery).
        recovered_from_status: The down status the gateway recovered from.
        reaped_json: JSON list of ``{sandbox, lost_providers}`` entries.
        reaped_count: Number of sandboxes reaped.
    """

    __tablename__ = "gateway_reap_records"
    __table_args__ = (Index("ix_gateway_reap_gw_time", "gateway", "detected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False)
    detected_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recovered_from_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    reaped_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reaped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UsageCursor(Base):
    """Log-poll cursor per sandbox (last metered log timestamp).

    Attributes:
        id: Auto-incremented primary key.
        gateway: Gateway name.
        sandbox: Sandbox name.
        last_ms: Timestamp (ms) of the newest log line already counted.
    """

    __tablename__ = "usage_cursors"
    __table_args__ = (UniqueConstraint("gateway", "sandbox", name="uq_usage_cursor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox: Mapped[str] = mapped_column(String(253), nullable=False)
    last_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
