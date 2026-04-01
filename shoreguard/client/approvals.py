"""Draft policy approval flow operations."""

from __future__ import annotations

from typing import Any

from ._converters import _dict_to_network_rule
from ._proto import openshell_pb2, openshell_pb2_grpc


def _chunk_to_dict(chunk: openshell_pb2.PolicyChunk) -> dict[str, Any]:
    """Convert a PolicyChunk protobuf to a plain dict.

    Args:
        chunk: PolicyChunk protobuf message.

    Returns:
        dict[str, Any]: Chunk data with status, rule, and metadata.
    """
    result: dict[str, Any] = {
        "id": chunk.id,
        "status": chunk.status,
        "rule_name": chunk.rule_name,
        "rationale": chunk.rationale,
        "security_notes": chunk.security_notes,
        "confidence": chunk.confidence,
        "created_at_ms": chunk.created_at_ms,
        "decided_at_ms": chunk.decided_at_ms,
        "stage": chunk.stage,
        "hit_count": chunk.hit_count,
        "first_seen_ms": chunk.first_seen_ms,
        "last_seen_ms": chunk.last_seen_ms,
        "binary": chunk.binary,
    }
    if chunk.HasField("proposed_rule"):
        rule = chunk.proposed_rule
        endpoints = []
        for ep in rule.endpoints:
            ep_dict: dict[str, Any] = {"host": ep.host, "port": ep.port}
            if ep.protocol:
                ep_dict["protocol"] = ep.protocol
            if ep.tls:
                ep_dict["tls"] = ep.tls
            if ep.enforcement:
                ep_dict["enforcement"] = ep.enforcement
            if ep.access:
                ep_dict["access"] = ep.access
            if ep.rules:
                ep_dict["rules"] = [
                    {
                        "allow": {
                            "method": r.allow.method,
                            "path": r.allow.path,
                            "command": r.allow.command,
                        }
                    }
                    for r in ep.rules
                ]
            if ep.allowed_ips:
                ep_dict["allowed_ips"] = list(ep.allowed_ips)
            if ep.ports:
                ep_dict["ports"] = list(ep.ports)
            endpoints.append(ep_dict)
        result["proposed_rule"] = {
            "name": rule.name,
            "endpoints": endpoints,
            "binaries": [{"path": b.path} for b in rule.binaries],
        }
    return result


class ApprovalManager:
    """Draft policy approval flow: review, approve, reject blocked requests.

    Args:
        stub: OpenShell gRPC stub.
        timeout: gRPC call timeout in seconds.
    """

    def __init__(self, stub: openshell_pb2_grpc.OpenShellStub, *, timeout: float = 30.0) -> None:  # noqa: D107
        self._stub = stub
        self._timeout = timeout

    def get_draft(self, sandbox_name: str, *, status_filter: str = "") -> dict[str, Any]:
        """Get draft policy recommendations for a sandbox.

        Args:
            sandbox_name: Sandbox name.
            status_filter: Optional status to filter chunks by.

        Returns:
            dict[str, Any]: Draft policy with chunks, summary, and version.
        """
        resp = self._stub.GetDraftPolicy(
            openshell_pb2.GetDraftPolicyRequest(name=sandbox_name, status_filter=status_filter),
            timeout=self._timeout,
        )
        return {
            "chunks": [_chunk_to_dict(c) for c in resp.chunks],
            "rolling_summary": resp.rolling_summary,
            "draft_version": resp.draft_version,
            "last_analyzed_at_ms": resp.last_analyzed_at_ms,
        }

    def get_pending(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get only pending (unapproved) draft chunks.

        Args:
            sandbox_name: Sandbox name.

        Returns:
            list[dict[str, Any]]: List of pending chunk dicts.
        """
        draft = self.get_draft(sandbox_name, status_filter="pending")
        return draft["chunks"]

    def approve(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Approve a single draft policy chunk (merges into active policy).

        Args:
            sandbox_name: Sandbox name.
            chunk_id: Chunk identifier to approve.

        Returns:
            dict[str, Any]: New policy version and hash.
        """
        resp = self._stub.ApproveDraftChunk(
            openshell_pb2.ApproveDraftChunkRequest(name=sandbox_name, chunk_id=chunk_id),
            timeout=self._timeout,
        )
        return {"policy_version": resp.policy_version, "policy_hash": resp.policy_hash}

    def reject(self, sandbox_name: str, chunk_id: str, *, reason: str = "") -> None:
        """Reject a single draft policy chunk.

        Args:
            sandbox_name: Sandbox name.
            chunk_id: Chunk identifier to reject.
            reason: Optional rejection reason.
        """
        self._stub.RejectDraftChunk(
            openshell_pb2.RejectDraftChunkRequest(
                name=sandbox_name, chunk_id=chunk_id, reason=reason
            ),
            timeout=self._timeout,
        )

    def approve_all(
        self, sandbox_name: str, *, include_security_flagged: bool = False
    ) -> dict[str, Any]:
        """Approve all pending draft chunks.

        Args:
            sandbox_name: Sandbox name.
            include_security_flagged: Whether to also approve
                security-flagged chunks.

        Returns:
            dict[str, Any]: Policy version, hash, and approval counts.
        """
        resp = self._stub.ApproveAllDraftChunks(
            openshell_pb2.ApproveAllDraftChunksRequest(
                name=sandbox_name, include_security_flagged=include_security_flagged
            ),
            timeout=self._timeout,
        )
        return {
            "policy_version": resp.policy_version,
            "policy_hash": resp.policy_hash,
            "chunks_approved": resp.chunks_approved,
            "chunks_skipped": resp.chunks_skipped,
        }

    def edit(self, sandbox_name: str, chunk_id: str, proposed_rule: dict) -> None:
        """Edit a pending draft chunk in-place.

        Args:
            sandbox_name: Sandbox name.
            chunk_id: Chunk identifier to edit.
            proposed_rule: Network rule dict to replace the existing rule.
        """
        rule = _dict_to_network_rule(proposed_rule)
        self._stub.EditDraftChunk(
            openshell_pb2.EditDraftChunkRequest(
                name=sandbox_name, chunk_id=chunk_id, proposed_rule=rule
            ),
            timeout=self._timeout,
        )

    def undo(self, sandbox_name: str, chunk_id: str) -> dict[str, Any]:
        """Reverse an approval (remove merged rule from active policy).

        Args:
            sandbox_name: Sandbox name.
            chunk_id: Chunk identifier to undo.

        Returns:
            dict[str, Any]: Updated policy version and hash.
        """
        resp = self._stub.UndoDraftChunk(
            openshell_pb2.UndoDraftChunkRequest(name=sandbox_name, chunk_id=chunk_id),
            timeout=self._timeout,
        )
        return {"policy_version": resp.policy_version, "policy_hash": resp.policy_hash}

    def clear(self, sandbox_name: str) -> dict[str, int]:
        """Clear all pending draft chunks for a sandbox.

        Args:
            sandbox_name: Sandbox name.

        Returns:
            dict[str, int]: Number of chunks cleared.
        """
        resp = self._stub.ClearDraftChunks(
            openshell_pb2.ClearDraftChunksRequest(name=sandbox_name),
            timeout=self._timeout,
        )
        return {"chunks_cleared": resp.chunks_cleared}

    def get_history(self, sandbox_name: str) -> list[dict[str, Any]]:
        """Get decision history for a sandbox's draft policy.

        Args:
            sandbox_name: Sandbox name.

        Returns:
            list[dict[str, Any]]: List of history entry dicts.
        """
        resp = self._stub.GetDraftHistory(
            openshell_pb2.GetDraftHistoryRequest(name=sandbox_name),
            timeout=self._timeout,
        )
        return [
            {
                "timestamp_ms": entry.timestamp_ms,
                "event_type": entry.event_type,
                "description": entry.description,
                "chunk_id": entry.chunk_id,
            }
            for entry in resp.entries
        ]
