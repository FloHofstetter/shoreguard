"""Policy management operations."""

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
    """Convert a SandboxPolicy protobuf to a plain dict."""
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
    """Convert a NetworkPolicyRule protobuf to dict."""
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
        result["endpoints"].append(ep_dict)
    for binary in rule.binaries:
        result["binaries"].append({"path": binary.path})
    return result


class PolicyManager:
    """Policy read/write operations against OpenShell gateway."""

    def __init__(self, stub: openshell_pb2_grpc.OpenShellStub, *, timeout: float = 30.0) -> None:
        """Initialize with an OpenShell gRPC stub."""
        self._stub = stub
        self._timeout = timeout

    def get(self, sandbox_name: str) -> dict[str, Any]:
        """Get the current active policy for a sandbox."""
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
        """Get a specific policy revision by version number."""
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
        """List policy revision history for a sandbox."""
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
        """Push a new policy version to a sandbox (or globally)."""
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
