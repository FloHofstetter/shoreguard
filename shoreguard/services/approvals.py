"""Draft policy approval flow service."""

from __future__ import annotations

from typing import Any

from shoreguard.client import ShoreGuardClient


class ApprovalService:
    """Draft policy approval operations shared by Web UI and TUI."""

    def __init__(self, client: ShoreGuardClient) -> None:
        """Initialize with an OpenShell client."""
        self._client = client

    def get_draft(self, sandbox_name: str, *, status_filter: str = "") -> dict[str, Any]:
        """Get draft policy recommendations for a sandbox."""
        return self._client.approvals.get_draft(sandbox_name, status_filter=status_filter)

    def get_pending(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get only pending (unapproved) draft chunks."""
        return self._client.approvals.get_pending(sandbox_name)

    def approve(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Approve a single draft policy chunk."""
        return self._client.approvals.approve(sandbox_name, chunk_id)

    def reject(self, sandbox_name: str, chunk_id: str, *, reason: str = "") -> None:
        """Reject a single draft policy chunk."""
        return self._client.approvals.reject(sandbox_name, chunk_id, reason=reason)

    def approve_all(
        self, sandbox_name: str, *, include_security_flagged: bool = False
    ) -> dict[str, Any]:
        """Approve all pending draft chunks."""
        return self._client.approvals.approve_all(
            sandbox_name, include_security_flagged=include_security_flagged
        )

    def edit(self, sandbox_name: str, chunk_id: str, proposed_rule: dict) -> None:
        """Edit a pending draft chunk's proposed rule."""
        return self._client.approvals.edit(sandbox_name, chunk_id, proposed_rule)

    def undo(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Reverse an approval decision."""
        return self._client.approvals.undo(sandbox_name, chunk_id)

    def clear(self, sandbox_name: str) -> dict[str, int]:
        """Clear all pending draft chunks for a sandbox."""
        return self._client.approvals.clear(sandbox_name)

    def get_history(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get decision history for a sandbox's draft policy."""
        return self._client.approvals.get_history(sandbox_name)
