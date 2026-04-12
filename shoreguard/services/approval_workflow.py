"""Multi-stage approval (quorum) service.

When a workflow is configured for a sandbox, approve/reject votes are
recorded locally. Upstream gateway approve fires only after the quorum
threshold is met; a single reject vote clears the chunk.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from shoreguard.models import ApprovalDecision, ApprovalWorkflow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm import sessionmaker as SessionMaker

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
approval_workflow_service: ApprovalWorkflowService | None = None


@dataclass
class VoteResult:
    """Outcome of recording a vote.

    Attributes:
        workflow: Workflow config as a dict.
        decisions: All decisions currently on the chunk.
        quorum_met: True if the configured number of approve-votes is reached.
        reject_seen: True if any decision is a reject vote.
        votes_needed: Absolute quorum threshold (for UI display).
        escalated: True if the chunk exceeded its escalation timeout.
    """

    workflow: dict[str, Any]
    decisions: list[dict[str, Any]]
    quorum_met: bool
    reject_seen: bool
    votes_needed: int
    escalated: bool


class ApprovalWorkflowService:
    """DB-backed quorum bookkeeping for draft approvals.

    Args:
        session_factory: SQLAlchemy session factory for database access.
    """

    def __init__(self, session_factory: SessionMaker) -> None:  # noqa: D107
        self._session_factory = session_factory

    # ── Workflow CRUD ──────────────────────────────────────────────────

    def get_workflow(self, gateway_name: str, sandbox_name: str) -> dict[str, Any] | None:
        """Return the workflow config for a sandbox, or None if unconfigured.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.

        Returns:
            dict[str, Any] | None: Workflow as a dict or None.
        """
        with self._session_factory() as session:
            row = self._get_workflow_row(session, gateway_name, sandbox_name)
            return self._workflow_to_dict(row) if row else None

    def upsert_workflow(
        self,
        gateway_name: str,
        sandbox_name: str,
        *,
        required_approvals: int,
        required_roles: list[str],
        distinct_actors: bool,
        escalation_timeout_minutes: int | None,
        actor: str,
    ) -> dict[str, Any]:
        """Create or replace the workflow for a sandbox.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the workflow applies to.
            required_approvals: Quorum threshold (must be ≥1).
            required_roles: Whitelist of roles eligible to vote; empty = any.
            distinct_actors: If true, the same actor cannot vote twice.
            escalation_timeout_minutes: Webhook-escalation timeout in minutes.
            actor: Identity of the admin configuring the workflow.

        Returns:
            dict[str, Any]: The stored workflow as a dict.

        Raises:
            ValueError: If ``required_approvals < 1``.
        """
        if required_approvals < 1:
            raise ValueError("required_approvals must be >= 1")

        with self._session_factory() as session:
            now = datetime.datetime.now(datetime.UTC)
            row = self._get_workflow_row(session, gateway_name, sandbox_name)
            roles_json = json.dumps(sorted(required_roles))
            if row is not None:
                row.required_approvals = required_approvals
                row.required_roles_json = roles_json
                row.distinct_actors = distinct_actors
                row.escalation_timeout_minutes = escalation_timeout_minutes
                row.updated_at = now
            else:
                row = ApprovalWorkflow(
                    gateway_name=gateway_name,
                    sandbox_name=sandbox_name,
                    required_approvals=required_approvals,
                    required_roles_json=roles_json,
                    distinct_actors=distinct_actors,
                    escalation_timeout_minutes=escalation_timeout_minutes,
                    created_by=actor,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            session.commit()
            session.refresh(row)
            return self._workflow_to_dict(row)

    def delete_workflow(self, gateway_name: str, sandbox_name: str) -> bool:
        """Delete the workflow (cascades to decisions).

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to deconfigure.

        Returns:
            bool: True if a workflow row was removed.
        """
        with self._session_factory() as session:
            row = self._get_workflow_row(session, gateway_name, sandbox_name)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    # ── Vote operations ────────────────────────────────────────────────

    def record_decision(
        self,
        gateway_name: str,
        sandbox_name: str,
        chunk_id: str,
        *,
        actor: str,
        role: str,
        decision: str,
        comment: str | None = None,
    ) -> VoteResult:
        """Record a vote and return the resulting quorum state.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the chunk belongs to.
            chunk_id: Draft chunk identifier.
            actor: Voting user identity.
            role: Role the voter currently holds.
            decision: ``approve`` or ``reject``.
            comment: Optional free-text comment.

        Returns:
            VoteResult: Workflow + decisions + quorum flags.

        Raises:
            LookupError: If no workflow is configured for the sandbox.
            PermissionError: If ``role`` is not in ``required_roles``.
            ValueError: If the actor already voted and ``distinct_actors`` is true.
        """
        if decision not in {"approve", "reject"}:
            raise ValueError(f"decision must be 'approve' or 'reject', got {decision!r}")

        with self._session_factory() as session:
            workflow = self._get_workflow_row(session, gateway_name, sandbox_name)
            if workflow is None:
                raise LookupError(f"No workflow configured for {gateway_name}/{sandbox_name}")

            required_roles = json.loads(workflow.required_roles_json or "[]")
            if required_roles and role not in required_roles:
                raise PermissionError(f"Role '{role}' not permitted (required: {required_roles})")

            existing = self._list_decision_rows(session, gateway_name, sandbox_name, chunk_id)
            if workflow.distinct_actors and any(d.actor == actor for d in existing):
                raise ValueError(f"Actor '{actor}' has already voted on chunk {chunk_id}")

            now = datetime.datetime.now(datetime.UTC)
            row = ApprovalDecision(
                workflow_id=workflow.id,
                gateway_name=gateway_name,
                sandbox_name=sandbox_name,
                chunk_id=chunk_id,
                actor=actor,
                role=role,
                decision=decision,
                comment=comment,
                created_at=now,
            )
            session.add(row)
            session.commit()

            decisions = self._list_decision_rows(session, gateway_name, sandbox_name, chunk_id)
            approvals = [d for d in decisions if d.decision == "approve"]
            reject_seen = any(d.decision == "reject" for d in decisions)
            quorum_met = not reject_seen and len(approvals) >= workflow.required_approvals

            escalated = self._check_escalation(workflow, decisions)

            result = VoteResult(
                workflow=self._workflow_to_dict(workflow),
                decisions=[self._decision_to_dict(d) for d in decisions],
                quorum_met=quorum_met,
                reject_seen=reject_seen,
                votes_needed=workflow.required_approvals,
                escalated=escalated,
            )

            # Clear rows eagerly once terminal state is reached — the upstream
            # approve/reject fires from the route handler after this returns.
            if quorum_met or reject_seen:
                session.execute(
                    delete(ApprovalDecision).where(
                        ApprovalDecision.gateway_name == gateway_name,
                        ApprovalDecision.sandbox_name == sandbox_name,
                        ApprovalDecision.chunk_id == chunk_id,
                    )
                )
                session.commit()

            return result

    def list_decisions(
        self, gateway_name: str, sandbox_name: str, chunk_id: str
    ) -> list[dict[str, Any]]:
        """Return all decisions currently on a chunk.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the chunk belongs to.
            chunk_id: Chunk identifier.

        Returns:
            list[dict[str, Any]]: Decision dicts sorted by ``created_at`` asc.
        """
        with self._session_factory() as session:
            rows = self._list_decision_rows(session, gateway_name, sandbox_name, chunk_id)
            return [self._decision_to_dict(d) for d in rows]

    def has_pending(self, gateway_name: str, sandbox_name: str) -> bool:
        """Return True if any decisions exist for the sandbox.

        Used by approve_all to refuse when workflow votes are mid-flight.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to check.

        Returns:
            bool: True if at least one decision row exists.
        """
        with self._session_factory() as session:
            row = session.execute(
                select(ApprovalDecision.id)
                .where(
                    ApprovalDecision.gateway_name == gateway_name,
                    ApprovalDecision.sandbox_name == sandbox_name,
                )
                .limit(1)
            ).first()
            return row is not None

    # ── Internals ──────────────────────────────────────────────────────

    @staticmethod
    def check_quorum(workflow: dict[str, Any], decisions: list[dict[str, Any]]) -> bool:
        """Pure helper: decide if the approve-quorum is met.

        Args:
            workflow: Workflow config dict.
            decisions: List of decision dicts.

        Returns:
            bool: True if quorum is reached and no reject is present.
        """
        if any(d["decision"] == "reject" for d in decisions):
            return False
        approvals = [d for d in decisions if d["decision"] == "approve"]
        return len(approvals) >= workflow["required_approvals"]

    @staticmethod
    def _check_escalation(workflow: ApprovalWorkflow, decisions: list[ApprovalDecision]) -> bool:
        """Check if escalation timeout has been exceeded.

        Args:
            workflow: Workflow ORM row.
            decisions: Current decision rows for the chunk.

        Returns:
            bool: True if escalation should fire.
        """
        if workflow.escalation_timeout_minutes is None or not decisions:
            return False
        first = min(decisions, key=lambda d: d.created_at)
        started = first.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=datetime.UTC)
        elapsed = datetime.datetime.now(datetime.UTC) - started
        return elapsed.total_seconds() >= workflow.escalation_timeout_minutes * 60

    @staticmethod
    def _get_workflow_row(
        session: Session, gateway_name: str, sandbox_name: str
    ) -> ApprovalWorkflow | None:
        """Fetch the workflow row, if any.

        Args:
            session: Active DB session.
            gateway_name: Gateway scope.
            sandbox_name: Sandbox scope.

        Returns:
            ApprovalWorkflow | None: Row if a workflow is configured, else None.
        """
        return session.execute(
            select(ApprovalWorkflow).where(
                ApprovalWorkflow.gateway_name == gateway_name,
                ApprovalWorkflow.sandbox_name == sandbox_name,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _list_decision_rows(
        session: Session, gateway_name: str, sandbox_name: str, chunk_id: str
    ) -> list[ApprovalDecision]:
        """Return decision rows for a chunk, ordered by creation time.

        Args:
            session: Active DB session.
            gateway_name: Gateway scope.
            sandbox_name: Sandbox scope.
            chunk_id: Chunk identifier.

        Returns:
            list[ApprovalDecision]: Decision rows in insertion order.
        """
        return list(
            session.execute(
                select(ApprovalDecision)
                .where(
                    ApprovalDecision.gateway_name == gateway_name,
                    ApprovalDecision.sandbox_name == sandbox_name,
                    ApprovalDecision.chunk_id == chunk_id,
                )
                .order_by(ApprovalDecision.created_at)
            )
            .scalars()
            .all()
        )

    @staticmethod
    def _workflow_to_dict(row: ApprovalWorkflow) -> dict[str, Any]:
        """Serialise a workflow row.

        Args:
            row: ApprovalWorkflow ORM row.

        Returns:
            dict[str, Any]: Plain dict with workflow config + timestamps.
        """
        return {
            "gateway_name": row.gateway_name,
            "sandbox_name": row.sandbox_name,
            "required_approvals": row.required_approvals,
            "required_roles": json.loads(row.required_roles_json or "[]"),
            "distinct_actors": bool(row.distinct_actors),
            "escalation_timeout_minutes": row.escalation_timeout_minutes,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    @staticmethod
    def _decision_to_dict(row: ApprovalDecision) -> dict[str, Any]:
        """Serialise a decision row.

        Args:
            row: ApprovalDecision ORM row.

        Returns:
            dict[str, Any]: Plain dict with actor, role, decision, comment.
        """
        return {
            "actor": row.actor,
            "role": row.role,
            "decision": row.decision,
            "comment": row.comment,
            "created_at": row.created_at.isoformat(),
        }
