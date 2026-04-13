"""Persist YAML policy-apply proposals across voter sessions.

When a multi-stage approval workflow is configured for a sandbox, a
``policy apply`` is not a single atomic action — the first caller
records one vote and then has to wait for additional voters. This
service caches the pending YAML payload in the database so the second
voter does not need to resubmit the same body, and so a process
restart between votes does not lose the in-flight proposal.

Rows are keyed by ``(gateway_name, sandbox_name, chunk_id)`` and use
the synthetic chunk id ``policy.apply:<sha16>`` so the existing
approval machinery can record votes against them without special
cases.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from shoreguard.models import PolicyApplyProposal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm import sessionmaker as SessionMaker

logger = logging.getLogger(__name__)

policy_apply_proposal_service: PolicyApplyProposalService | None = None


class PolicyApplyProposalService:
    """Database-backed cache of in-flight policy-apply proposals.

    Used when a sandbox has an active quorum-approval workflow: the
    first voter's YAML body is stored here so subsequent voters can
    add their votes without having to resubmit the same payload, and
    so a restart does not lose the proposal mid-vote. Idempotent on
    ``upsert`` for the same chunk id.

    Args:
        session_factory: SQLAlchemy sync session factory used to open
            short-lived sessions for each call.
    """

    def __init__(self, session_factory: SessionMaker) -> None:  # noqa: D107
        self._session_factory = session_factory

    def upsert(
        self,
        gateway_name: str,
        sandbox_name: str,
        chunk_id: str,
        *,
        yaml_text: str,
        expected_hash: str | None,
        proposed_by: str,
    ) -> dict[str, Any]:
        """Insert or refresh a proposal row keyed by chunk_id.

        If a proposal for the same (gateway, sandbox, chunk_id) already
        exists, the YAML body is replaced and ``proposed_at`` is bumped —
        idempotent for re-submission of the same chunk by additional voters.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the apply targets.
            chunk_id: Synthetic chunk id ``policy.apply:<sha16>``.
            yaml_text: Raw YAML body.
            expected_hash: Optimistic-lock etag captured at proposal time.
            proposed_by: Identity of the actor opening the proposal.

        Returns:
            dict[str, Any]: The stored proposal.
        """
        with self._session_factory() as session:
            existing = self._get_row(session, gateway_name, sandbox_name, chunk_id)
            now = datetime.datetime.now(datetime.UTC)
            if existing is not None:
                existing.yaml_text = yaml_text
                existing.expected_hash = expected_hash
                existing.proposed_by = proposed_by
                existing.proposed_at = now
                session.commit()
                session.refresh(existing)
                return self._to_dict(existing)
            row = PolicyApplyProposal(
                gateway_name=gateway_name,
                sandbox_name=sandbox_name,
                chunk_id=chunk_id,
                yaml_text=yaml_text,
                expected_hash=expected_hash,
                proposed_by=proposed_by,
                proposed_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_dict(row)

    def get(self, gateway_name: str, sandbox_name: str, chunk_id: str) -> dict[str, Any] | None:
        """Return a proposal by composite key, or None.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the apply targets.
            chunk_id: Synthetic chunk id.

        Returns:
            dict[str, Any] | None: Proposal row as dict, or None.
        """
        with self._session_factory() as session:
            row = self._get_row(session, gateway_name, sandbox_name, chunk_id)
            return self._to_dict(row) if row else None

    def delete(self, gateway_name: str, sandbox_name: str, chunk_id: str) -> bool:
        """Delete a proposal.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the apply targets.
            chunk_id: Synthetic chunk id.

        Returns:
            bool: True if a row was removed, False otherwise.
        """
        with self._session_factory() as session:
            result = session.execute(
                delete(PolicyApplyProposal).where(
                    PolicyApplyProposal.gateway_name == gateway_name,
                    PolicyApplyProposal.sandbox_name == sandbox_name,
                    PolicyApplyProposal.chunk_id == chunk_id,
                )
            )
            session.commit()
            return result.rowcount > 0  # type: ignore[union-attr]

    def list_for_sandbox(self, gateway_name: str, sandbox_name: str) -> list[dict[str, Any]]:
        """List all open proposals for a sandbox (most recent first).

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the proposals belong to.

        Returns:
            list[dict[str, Any]]: Proposal rows as dicts.
        """
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(PolicyApplyProposal)
                    .where(
                        PolicyApplyProposal.gateway_name == gateway_name,
                        PolicyApplyProposal.sandbox_name == sandbox_name,
                    )
                    .order_by(PolicyApplyProposal.proposed_at.desc())
                )
                .scalars()
                .all()
            )
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _get_row(
        session: Session, gateway_name: str, sandbox_name: str, chunk_id: str
    ) -> PolicyApplyProposal | None:
        return session.execute(
            select(PolicyApplyProposal).where(
                PolicyApplyProposal.gateway_name == gateway_name,
                PolicyApplyProposal.sandbox_name == sandbox_name,
                PolicyApplyProposal.chunk_id == chunk_id,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _to_dict(row: PolicyApplyProposal) -> dict[str, Any]:
        return {
            "gateway_name": row.gateway_name,
            "sandbox_name": row.sandbox_name,
            "chunk_id": row.chunk_id,
            "yaml_text": row.yaml_text,
            "expected_hash": row.expected_hash,
            "proposed_by": row.proposed_by,
            "proposed_at": row.proposed_at.isoformat(),
        }
