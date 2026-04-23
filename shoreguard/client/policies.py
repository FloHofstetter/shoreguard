"""gRPC wrapper for OpenShell's sandbox policy RPCs.

Exposes read and write operations against a sandbox's policy via
``GetSandbox`` / ``UpdateConfig`` / ``ListPolicyRevisions`` /
``DiffPolicyRevisions``. Converts protobuf ``PolicyChunk`` /
``NetworkRule`` / ``FilesystemPath`` / ``ProcessPolicy`` messages
to plain dicts through the shared converters so every caller
sees the same JSON-ready shape.

This manager is deliberately stateless: it holds the stub and
nothing else. Atomic single-rule CRUD (read-modify-write over
whole-policy updates) lives in
:class:`~shoreguard.services.policy.PolicyService`, not here,
because the read-modify-write loop needs to be aware of
pinning, audit logging, and denial-context capture.
"""

from __future__ import annotations

from typing import Any

from ._proto import openshell_pb2, openshell_pb2_grpc, sandbox_pb2

POLICY_STATUS_NAMES = {
    0: "unspecified",
    1: "pending",
    2: "loaded",
    3: "failed",
    4: "superseded",
}


def _policy_to_dict(policy: sandbox_pb2.SandboxPolicy) -> dict[str, Any]:
    """Convert a SandboxPolicy protobuf to a plain dict.

    Args:
        policy: SandboxPolicy protobuf message.

    Returns:
        dict[str, Any]: Policy data with version, filesystem, process,
            landlock, and network_policies.
    """
    result: dict[str, Any] = {"version": policy.version}

    if policy.HasField("filesystem"):
        result["filesystem"] = {
            "include_workdir": policy.filesystem.include_workdir,
            "read_only": list(policy.filesystem.read_only),
            "read_write": list(policy.filesystem.read_write),
        }
    if policy.HasField("process"):
        result["process"] = {
            "run_as_user": policy.process.run_as_user,
            "run_as_group": policy.process.run_as_group,
        }
    if policy.HasField("landlock"):
        result["landlock"] = {"compatibility": policy.landlock.compatibility}

    network_policies: dict[str, Any] = {}
    for key, rule in policy.network_policies.items():
        network_policies[key] = _network_rule_to_dict(rule)
    if network_policies:
        result["network_policies"] = network_policies
    return result


def _network_rule_to_dict(rule: sandbox_pb2.NetworkPolicyRule) -> dict[str, Any]:
    """Convert a NetworkPolicyRule protobuf to dict.

    Args:
        rule: NetworkPolicyRule protobuf message.

    Returns:
        dict[str, Any]: Network rule data with name, endpoints,
            and binaries.
    """
    result: dict[str, Any] = {"name": rule.name, "endpoints": [], "binaries": []}
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
        if ep.allow_encoded_slash:
            ep_dict["allow_encoded_slash"] = True
        if ep.rules:
            rules_list = []
            for r in ep.rules:
                allow_dict: dict[str, Any] = {
                    "method": r.allow.method,
                    "path": r.allow.path,
                    "command": r.allow.command,
                }
                if r.allow.query:
                    allow_dict["query"] = {
                        key: {
                            **({"glob": m.glob} if m.glob else {}),
                            **({"any": list(m.any)} if m.any else {}),
                        }
                        for key, m in r.allow.query.items()
                    }
                rules_list.append({"allow": allow_dict})
            ep_dict["rules"] = rules_list
        if ep.deny_rules:
            deny_list = []
            for d in ep.deny_rules:
                deny_dict: dict[str, Any] = {
                    "method": d.method,
                    "path": d.path,
                    "command": d.command,
                }
                if d.query:
                    deny_dict["query"] = {
                        key: {
                            **({"glob": m.glob} if m.glob else {}),
                            **({"any": list(m.any)} if m.any else {}),
                        }
                        for key, m in d.query.items()
                    }
                deny_list.append(deny_dict)
            ep_dict["deny_rules"] = deny_list
        if ep.allowed_ips:
            ep_dict["allowed_ips"] = list(ep.allowed_ips)
        if ep.ports:
            ep_dict["ports"] = list(ep.ports)
        result["endpoints"].append(ep_dict)
    for binary in rule.binaries:
        result["binaries"].append({"path": binary.path})
    return result


class PolicyManager:
    """Policy read/write operations against OpenShell gateway.

    Args:
        stub: OpenShell gRPC stub.
        timeout: gRPC call timeout in seconds.
    """

    def __init__(self, stub: openshell_pb2_grpc.OpenShellStub, *, timeout: float = 30.0) -> None:  # noqa: D107
        self._stub = stub
        self._timeout = timeout

    def get(self, sandbox_name: str) -> dict[str, Any]:
        """Get the current active policy for a sandbox.

        Args:
            sandbox_name: Sandbox name.

        Returns:
            dict[str, Any]: Active policy status with revision details.
        """
        resp = self._stub.GetSandboxPolicyStatus(
            openshell_pb2.GetSandboxPolicyStatusRequest(name=sandbox_name),
            timeout=self._timeout,
        )
        result: dict[str, Any] = {
            "active_version": resp.active_version,
            "revision": {
                "version": resp.revision.version,
                "status": POLICY_STATUS_NAMES.get(resp.revision.status, "unknown"),
                "policy_hash": resp.revision.policy_hash,
                "created_at_ms": resp.revision.created_at_ms,
                "loaded_at_ms": resp.revision.loaded_at_ms,
            },
        }
        if resp.revision.HasField("policy"):
            result["policy"] = _policy_to_dict(resp.revision.policy)
        return result

    def get_version(self, sandbox_name: str, version: int) -> dict[str, Any]:
        """Get a specific policy revision by version number.

        Args:
            sandbox_name: Sandbox name.
            version: Policy version number to retrieve.

        Returns:
            dict[str, Any]: Policy status with revision details.
        """
        resp = self._stub.GetSandboxPolicyStatus(
            openshell_pb2.GetSandboxPolicyStatusRequest(name=sandbox_name, version=version),
            timeout=self._timeout,
        )
        result: dict[str, Any] = {
            "active_version": resp.active_version,
            "revision": {
                "version": resp.revision.version,
                "status": POLICY_STATUS_NAMES.get(resp.revision.status, "unknown"),
                "policy_hash": resp.revision.policy_hash,
                "created_at_ms": resp.revision.created_at_ms,
                "loaded_at_ms": resp.revision.loaded_at_ms,
            },
        }
        if resp.revision.HasField("policy"):
            result["policy"] = _policy_to_dict(resp.revision.policy)
        return result

    def list_revisions(
        self, sandbox_name: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List policy revision history for a sandbox.

        Args:
            sandbox_name: Sandbox name.
            limit: Maximum number of revisions to return.
            offset: Pagination offset.

        Returns:
            list[dict[str, Any]]: List of policy revision summary dicts.
        """
        resp = self._stub.ListSandboxPolicies(
            openshell_pb2.ListSandboxPoliciesRequest(name=sandbox_name, limit=limit, offset=offset),
            timeout=self._timeout,
        )
        return [
            {
                "version": rev.version,
                "status": POLICY_STATUS_NAMES.get(rev.status, "unknown"),
                "policy_hash": rev.policy_hash,
                "created_at_ms": rev.created_at_ms,
                "loaded_at_ms": rev.loaded_at_ms,
                "load_error": rev.load_error,
            }
            for rev in resp.revisions
        ]

    def update(
        self, sandbox_name: str, policy: sandbox_pb2.SandboxPolicy, *, global_scope: bool = False
    ) -> dict[str, Any]:
        """Push a new policy version to a sandbox (or globally).

        Args:
            sandbox_name: Sandbox name.
            policy: SandboxPolicy protobuf message.
            global_scope: If True, apply policy globally.

        Returns:
            dict[str, Any]: Version and policy hash of the new revision.
        """
        resp = self._stub.UpdateConfig(
            openshell_pb2.UpdateConfigRequest(
                name=sandbox_name,
                policy=policy,
                **{"global": global_scope},  # type: ignore[arg-type]
            ),
            timeout=self._timeout,
        )
        return {
            "version": resp.version,
            "policy_hash": resp.policy_hash,
        }

    def submit_analysis(
        self,
        sandbox_name: str,
        *,
        summaries: list[dict[str, Any]],
        proposed_chunks: list[dict[str, Any]],
        analysis_mode: str = "",
    ) -> dict[str, Any]:
        """Submit denial-analysis results and proposed chunks to the gateway.

        The gateway merges accepted chunks into the draft policy and rejects
        the rest with a per-chunk reason. Used by external analyzers
        (LLM-backed or rule-based) that observe sandbox denials and propose
        policy fixes.

        Args:
            sandbox_name: Target sandbox name — goes into ``request.name``.
            summaries: ``DenialSummary`` dicts. Unknown keys raise
                ``TypeError`` from the proto constructor — the caller is
                responsible for sending only fields that the currently
                pinned OpenShell proto defines.
            proposed_chunks: ``PolicyChunk`` dicts with the rules that would
                fix the denials described in *summaries*.
            analysis_mode: Optional mode tag forwarded verbatim, e.g.
                ``"auto"`` or ``"manual"``.

        Returns:
            dict[str, Any]: ``{"accepted_chunks": int, "rejected_chunks": int,
            "rejection_reasons": list[str]}``.
        """
        req = openshell_pb2.SubmitPolicyAnalysisRequest(
            name=sandbox_name,
            analysis_mode=analysis_mode,
            summaries=[openshell_pb2.DenialSummary(**s) for s in summaries],
            proposed_chunks=[openshell_pb2.PolicyChunk(**c) for c in proposed_chunks],
        )
        resp = self._stub.SubmitPolicyAnalysis(req, timeout=self._timeout)
        return {
            "accepted_chunks": resp.accepted_chunks,
            "rejected_chunks": resp.rejected_chunks,
            "rejection_reasons": list(resp.rejection_reasons),
        }
