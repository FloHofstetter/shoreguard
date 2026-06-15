"""Draft-policy approval workflow wrapper over the gRPC approval RPCs.

OpenShell's approval flow works in terms of *draft policy chunks*:
each time a denial fires, the gateway emits a chunk that proposes
the rules that would allow the blocked action. This service
mediates the operator-facing view of that flow — listing pending
chunks, approving / rejecting / editing individual ones, and
supporting bulk approve-all and undo operations.

The service is deliberately thin: almost every call forwards
directly to ``ShoreGuardClient.approvals`` with additional
request-level validation and audit logging. Multi-stage quorum
is not handled here — that lives in
:class:`~shoreguard.services.approval_workflow.ApprovalWorkflowService`,
which the approval route consults *before* calling through to
this service.
"""

from __future__ import annotations

from typing import Any

from shoreguard.client import ShoreGuardClient


class ApprovalService:
    """Draft policy approval operations shared by Web UI and TUI.

    Args:
        client: OpenShell gRPC client instance.
    """

    def __init__(self, client: ShoreGuardClient) -> None:  # noqa: D107
        self._client = client

    async def get_draft(self, sandbox_name: str, *, status_filter: str = "") -> dict[str, Any]:
        """Get draft policy recommendations for a sandbox.

        Args:
            sandbox_name: Name of the sandbox.
            status_filter: Optional status to filter by.

        Returns:
            dict[str, Any]: Draft policy data with denial context enrichment.
        """
        result = await self._client.approvals.get_draft(sandbox_name, status_filter=status_filter)

        from shoreguard.container import try_get_container
        from shoreguard.services.policy_simulator import annotate_narrowness
        from shoreguard.settings import get_settings

        container = try_get_container()
        chunks = result.get("chunks", [])
        if container is not None:
            container.denial_context.enrich_chunks(sandbox_name, chunks)
        if get_settings().simulator.narrowness_gate_enabled:
            annotate_narrowness(chunks)

        return result

    async def get_pending(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get only pending (unapproved) draft chunks.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            list[dict[str, Any]]: Pending draft chunks with denial context.
        """
        chunks = await self._client.approvals.get_pending(sandbox_name)

        from shoreguard.container import try_get_container
        from shoreguard.services.policy_simulator import annotate_narrowness
        from shoreguard.settings import get_settings

        container = try_get_container()
        if container is not None:
            container.denial_context.enrich_chunks(sandbox_name, chunks)
        if get_settings().simulator.narrowness_gate_enabled:
            annotate_narrowness(chunks)

        return chunks

    async def approve(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Approve a single draft policy chunk.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to approve.

        Returns:
            dict[str, Any]: Updated chunk data.
        """
        return await self._client.approvals.approve(sandbox_name, chunk_id)

    async def reject(self, sandbox_name: str, chunk_id: str, *, reason: str = "") -> None:
        """Reject a single draft policy chunk.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to reject.
            reason: Optional reason for rejection.
        """
        return await self._client.approvals.reject(sandbox_name, chunk_id, reason=reason)

    async def approve_all(
        self, sandbox_name: str, *, include_security_flagged: bool = False
    ) -> dict[str, Any]:
        """Approve all pending draft chunks.

        Args:
            sandbox_name: Name of the sandbox.
            include_security_flagged: Whether to include security-flagged chunks.

        Returns:
            dict[str, Any]: Summary of approved chunks.
        """
        return await self._client.approvals.approve_all(
            sandbox_name, include_security_flagged=include_security_flagged
        )

    async def edit(self, sandbox_name: str, chunk_id: str, proposed_rule: dict) -> None:
        """Edit a pending draft chunk's proposed rule.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to edit.
            proposed_rule: New proposed rule content.
        """
        return await self._client.approvals.edit(sandbox_name, chunk_id, proposed_rule)

    async def undo(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Reverse an approval decision.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to undo.

        Returns:
            dict[str, Any]: Updated chunk data.
        """
        return await self._client.approvals.undo(sandbox_name, chunk_id)

    async def clear(self, sandbox_name: str) -> dict[str, int]:
        """Clear all pending draft chunks for a sandbox.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            dict[str, int]: Count of cleared chunks.
        """
        return await self._client.approvals.clear(sandbox_name)

    async def get_history(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get decision history for a sandbox's draft policy.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            list[dict[str, Any]]: Decision history entries.
        """
        return await self._client.approvals.get_history(sandbox_name)
