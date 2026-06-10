"""Draft approval and approval-workflow schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ─── Approvals ────────────────────────────────────────────────────────────────


class ApprovalDraftResponse(BaseModel):
    """Draft policy with approval metadata.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class ApprovalChunkResponse(BaseModel):
    """Single approval chunk status.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class ApprovalBulkResponse(BaseModel):
    """Bulk approval result with counts.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class ApprovalClearResponse(BaseModel):
    """Approval clear result.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        cleared (int | None): Number of approvals cleared.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    cleared: int | None = None


class ApprovalWorkflowConfig(BaseModel):
    """Configuration for a multi-stage approval workflow.

    Attributes:
        required_approvals (int): Number of distinct approve votes needed.
        required_roles (list[str]): Roles eligible to vote (empty = any).
        distinct_actors (bool): If true, the same actor cannot vote twice.
        escalation_timeout_minutes (int | None): Escalation timeout in
            minutes; ``None`` disables escalation.
    """

    required_approvals: int = Field(ge=1, le=20, default=2)
    required_roles: list[str] = Field(default_factory=list)
    distinct_actors: bool = True
    escalation_timeout_minutes: int | None = Field(default=None, ge=1, le=10080)


class ApprovalWorkflowResponse(BaseModel):
    """Stored workflow configuration returned to clients.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")


class ApprovalDecisionEntry(BaseModel):
    """A single recorded vote on an approval chunk.

    Attributes:
        actor (str): Voting user identity.
        role (str): Role at vote time.
        decision (str): ``approve`` or ``reject``.
        comment (str | None): Optional comment.
        created_at (str): ISO-8601 vote timestamp.
    """

    actor: str
    role: str
    decision: str
    comment: str | None = None
    created_at: str


class ApprovalVoteResponse(BaseModel):
    """Response body when a vote is recorded but quorum is not yet met.

    Returned with HTTP 202 from ``/approve`` or ``/reject`` under an active
    workflow. For the approved-path response (quorum met), the normal
    ``ApprovalChunkResponse`` shape is returned instead.

    Attributes:
        status (str): ``pending``, ``approved``, or ``rejected``.
        votes (int): Approve votes recorded so far.
        needed (int): Quorum threshold.
        decisions (list[ApprovalDecisionEntry]): Current decision rows.
    """

    status: str
    votes: int
    needed: int
    decisions: list[ApprovalDecisionEntry] = Field(default_factory=list)
