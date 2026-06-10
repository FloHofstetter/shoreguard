"""Policy pin, approval workflow, and apply-proposal models."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shoreguard.db.models.base import Base


class PolicyPin(Base):
    """A policy pin that locks a sandbox's policy at a specific version.

    When a pin is active, policy updates and draft approvals are blocked
    until the pin is removed or expires.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Name of the gateway the sandbox belongs to.
        sandbox_name: Name of the pinned sandbox.
        pinned_version: The policy version that is locked.
        pinned_by: Email or service principal name of the actor who set the pin.
        reason: Optional human-readable reason for pinning.
        pinned_at: Timestamp when the pin was created.
        expires_at: Optional expiry timestamp; ``None`` means pin never expires.
    """

    __tablename__ = "policy_pins"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    pinned_version: Mapped[int] = mapped_column(Integer, nullable=False)
    pinned_by: Mapped[str] = mapped_column(String(254), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    pinned_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalWorkflow(Base):
    """A multi-stage approval (quorum) configuration for a sandbox.

    When a workflow exists, ``POST .../approvals/{chunk_id}/approve`` records
    a vote rather than calling the upstream gateway directly. The upstream
    approve fires only when the configured quorum is reached.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox this workflow applies to.
        required_approvals: Number of distinct approve votes needed.
        required_roles_json: JSON array of roles eligible to vote (empty = any).
        distinct_actors: If true, the same actor cannot vote twice.
        escalation_timeout_minutes: Fire ``approval.escalated`` webhook after
            this many minutes since the first vote on a chunk; ``None`` = off.
        created_by: Identity of the admin who configured the workflow.
        created_at: When the workflow was created.
        updated_at: When the workflow was last updated.
    """

    __tablename__ = "approval_workflows"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    required_roles_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    distinct_actors: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    escalation_timeout_minutes: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalDecision(Base):
    """A single vote cast against an approval chunk under a workflow.

    Append-only log; pending/approved/rejected state is derived from the row
    set. Rows are cleared once the upstream gateway approve fires (on quorum
    met) or the chunk is rejected.

    Attributes:
        id: Auto-incremented primary key.
        workflow_id: FK to the active workflow configuration.
        gateway_name: Gateway the sandbox belongs to (denormalised for lookup).
        sandbox_name: Sandbox the chunk belongs to (denormalised for lookup).
        chunk_id: The draft chunk being voted on.
        actor: Identity of the voting user.
        role: Role the voter held at vote time.
        decision: ``approve`` or ``reject``.
        comment: Optional free-text comment.
        created_at: When the vote was cast.
    """

    __tablename__ = "approval_decisions"
    __table_args__ = (
        Index(
            "ix_approval_decisions_chunk",
            "gateway_name",
            "sandbox_name",
            "chunk_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("approval_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(254), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PolicyApplyProposal(Base):
    """A YAML policy apply proposal waiting for workflow quorum.

    Created on the first apply call for a sandbox with an active
    quorum approval workflow, and deleted once the proposal reaches
    a terminal state (quorum met, rejected, or superseded by a new
    YAML body). Lets subsequent vote-only calls reference the same
    proposal by its synthetic ``chunk_id`` without requiring the
    second runner to resubmit the YAML body — useful when the
    second voter is a human on the UI rather than the same CI
    pipeline.

    Attributes:
        id: Auto-incremented primary key.
        gateway_name: Gateway the sandbox belongs to.
        sandbox_name: Sandbox the apply targets.
        chunk_id: Synthetic chunk id ``policy.apply:<sha16>`` derived from yaml.
        yaml_text: Raw YAML document body.
        expected_hash: Optimistic-lock etag captured at proposal time.
        proposed_by: Identity of the actor that opened the proposal.
        proposed_at: When the proposal was created.
    """

    __tablename__ = "policy_apply_proposals"
    __table_args__ = (UniqueConstraint("gateway_name", "sandbox_name", "chunk_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway_name: Mapped[str] = mapped_column(String(253), nullable=False)
    sandbox_name: Mapped[str] = mapped_column(String(253), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(80), nullable=False)
    yaml_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_hash: Mapped[str | None] = mapped_column(String(80))
    proposed_by: Mapped[str] = mapped_column(String(254), nullable=False)
    proposed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
