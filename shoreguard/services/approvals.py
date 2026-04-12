"""Draft policy approval flow service."""

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

    def get_draft(self, sandbox_name: str, *, status_filter: str = "") -> dict[str, Any]:
        """Get draft policy recommendations for a sandbox.

        Args:
            sandbox_name: Name of the sandbox.
            status_filter: Optional status to filter by.

        Returns:
            dict[str, Any]: Draft policy data with denial context enrichment.
        """
        result = self._client.approvals.get_draft(sandbox_name, status_filter=status_filter)

        from shoreguard.services.denial_context import denial_context_service

        if denial_context_service is not None:
            denial_context_service.enrich_chunks(sandbox_name, result.get("chunks", []))

        return result

    def get_pending(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get only pending (unapproved) draft chunks.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            list[dict[str, Any]]: Pending draft chunks with denial context.
        """
        chunks = self._client.approvals.get_pending(sandbox_name)

        from shoreguard.services.denial_context import denial_context_service

        if denial_context_service is not None:
            denial_context_service.enrich_chunks(sandbox_name, chunks)

        return chunks

    def approve(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Approve a single draft policy chunk.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to approve.

        Returns:
            dict[str, Any]: Updated chunk data.
        """
        return self._client.approvals.approve(sandbox_name, chunk_id)

    def reject(self, sandbox_name: str, chunk_id: str, *, reason: str = "") -> None:
        """Reject a single draft policy chunk.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to reject.
            reason: Optional reason for rejection.
        """
        return self._client.approvals.reject(sandbox_name, chunk_id, reason=reason)

    def approve_all(
        self, sandbox_name: str, *, include_security_flagged: bool = False
    ) -> dict[str, Any]:
        """Approve all pending draft chunks.

        Args:
            sandbox_name: Name of the sandbox.
            include_security_flagged: Whether to include security-flagged chunks.

        Returns:
            dict[str, Any]: Summary of approved chunks.
        """
        return self._client.approvals.approve_all(
            sandbox_name, include_security_flagged=include_security_flagged
        )

    def edit(self, sandbox_name: str, chunk_id: str, proposed_rule: dict) -> None:
        """Edit a pending draft chunk's proposed rule.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to edit.
            proposed_rule: New proposed rule content.
        """
        return self._client.approvals.edit(sandbox_name, chunk_id, proposed_rule)

    def undo(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Reverse an approval decision.

        Args:
            sandbox_name: Name of the sandbox.
            chunk_id: Identifier of the chunk to undo.

        Returns:
            dict[str, Any]: Updated chunk data.
        """
        return self._client.approvals.undo(sandbox_name, chunk_id)

    def clear(self, sandbox_name: str) -> dict[str, int]:
        """Clear all pending draft chunks for a sandbox.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            dict[str, int]: Count of cleared chunks.
        """
        return self._client.approvals.clear(sandbox_name)

    def get_history(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get decision history for a sandbox's draft policy.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            list[dict[str, Any]]: Decision history entries.
        """
        return self._client.approvals.get_history(sandbox_name)
