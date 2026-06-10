"""Policy read/diff/export/apply/analysis schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Policies ─────────────────────────────────────────────────────────────────


class PolicyResponse(BaseModel):
    """Policy document (dynamic structure from gateway).

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class PolicyDiffResponse(BaseModel):
    """Diff between two policy revisions.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version


class PolicyExportResponse(BaseModel):
    """Response body for GET /sandboxes/{name}/policy/export.

    Attributes:
        yaml: YAML document with metadata + policy blocks.
        gateway: Gateway name (mirrors metadata.gateway).
        sandbox: Sandbox name.
        version: Active policy version (may be 0 if no revision exists).
        policy_hash: OpenShell-computed policy hash (etag for optimistic locking).
    """

    yaml: str
    gateway: str
    sandbox: str
    version: int
    policy_hash: str


class PolicyApplyRequest(BaseModel):
    """Request body for POST /sandboxes/{name}/policy/apply.

    Attributes:
        yaml: YAML document body (with optional metadata block).
        dry_run: When true, compute diff without writing.
        expected_version: Optional optimistic-lock etag (overrides metadata).
        mode: Apply mode. ``replace`` (default) writes the full target
            policy as one ``UpdateConfigRequest.policy`` message — the
            historical behaviour. ``merge`` computes the rule-level
            diff against the current policy and sends only the
            resulting ``merge_operations`` (upstream OpenShell ≥
            v0.0.33). ``merge`` mode is rejected when the diff touches
            sections outside ``network_policies`` — callers should
            retry with ``replace``.
    """

    yaml: str
    dry_run: bool = False
    expected_version: str | None = None
    mode: Literal["replace", "merge"] = "replace"


class PolicyApplyResponse(BaseModel):
    """Response body for POST /sandboxes/{name}/policy/apply.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        status (str): One of ``up_to_date``, ``dry_run``, ``applied``,
            ``vote_recorded``, ``rejected``.
        current_hash (str): Server policy hash before this call.
        diff (dict): Structured policy diff.
        applied_version (str | None): Hash after the apply (only on ``applied``).
        votes_needed (int | None): Required approval count if under workflow.
        votes_cast (int | None): Approve votes already recorded if under workflow.
    """

    model_config = ConfigDict(extra="allow")

    status: str
    current_hash: str
    diff: dict
    applied_version: str | None = None
    votes_needed: int | None = None
    votes_cast: int | None = None


class PolicyAnalysisRequest(BaseModel):
    """Request body for POST /sandboxes/{name}/policy/analysis.

    Pass-through envelope for the OpenShell ``SubmitPolicyAnalysis`` RPC.
    The two list fields are dicts shaped like the upstream
    ``DenialSummary`` and ``PolicyChunk`` proto messages; ShoreGuard
    does not duplicate the proto schemas in Pydantic because the field
    set is large (33 fields combined) and will drift with OpenShell
    releases. Unknown keys in the dicts surface as ``TypeError`` from
    the proto constructor at the client layer.

    Attributes:
        model_config (ConfigDict): Pydantic config (unknown top-level
            fields rejected).
        summaries (list[dict[str, Any]]): ``DenialSummary`` dicts.
        proposed_chunks (list[dict[str, Any]]): ``PolicyChunk`` dicts —
            the rules that would fix the denials in *summaries*.
        analysis_mode (str): Opaque mode tag forwarded verbatim to the
            gateway (e.g. ``"auto"``, ``"manual"``).
    """

    model_config = ConfigDict(extra="forbid")

    summaries: list[dict[str, Any]] = Field(default_factory=list)
    proposed_chunks: list[dict[str, Any]] = Field(default_factory=list)
    analysis_mode: str = ""


class PolicyAnalysisResponse(BaseModel):
    """Response body for POST /sandboxes/{name}/policy/analysis.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed
            to accommodate upstream proto drift).
        accepted_chunks (int): Number of proposed chunks merged into the
            draft policy.
        rejected_chunks (int): Number of proposed chunks the gateway
            rejected.
        rejection_reasons (list[str]): Per-rejection reason strings from
            the gateway, aligned by index with the rejected subset.
    """

    model_config = ConfigDict(extra="allow")

    accepted_chunks: int
    rejected_chunks: int
    rejection_reasons: list[str] = Field(default_factory=list)


class PresetSummaryResponse(BaseModel):
    """Policy preset list entry.

    Attributes:
        model_config (ConfigDict): Pydantic config (extra fields allowed).
        name (str | None): Preset name.
        description (str | None): Human-readable preset description.
    """

    model_config = ConfigDict(extra="allow")
    # extra="allow": structure depends on gateway protocol version

    name: str | None = None
    description: str | None = None
