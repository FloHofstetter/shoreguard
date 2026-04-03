"""Protobuf ↔ dict conversion helpers shared across client managers."""

from __future__ import annotations

from ._proto import sandbox_pb2


def _dict_to_policy(data: dict) -> sandbox_pb2.SandboxPolicy:
    """Convert a policy dict to protobuf SandboxPolicy.

    Args:
        data: Policy definition with optional keys ``version``,
            ``filesystem``/``filesystem_policy``, ``process``,
            ``landlock``, and ``network_policies``.

    Returns:
        sandbox_pb2.SandboxPolicy: Populated protobuf message.
    """
    policy = sandbox_pb2.SandboxPolicy()
    if "version" in data:
        policy.version = data["version"]
    if "filesystem" in data or "filesystem_policy" in data:
        fs = data.get("filesystem") or data.get("filesystem_policy", {})
        policy.filesystem.CopyFrom(
            sandbox_pb2.FilesystemPolicy(
                include_workdir=fs.get("include_workdir", False),
                read_only=fs.get("read_only", []),
                read_write=fs.get("read_write", []),
            )
        )
    if "process" in data:
        policy.process.CopyFrom(
            sandbox_pb2.ProcessPolicy(
                run_as_user=data["process"].get("run_as_user", ""),
                run_as_group=data["process"].get("run_as_group", ""),
            )
        )
    if "landlock" in data:
        policy.landlock.CopyFrom(
            sandbox_pb2.LandlockPolicy(
                compatibility=data["landlock"].get("compatibility", ""),
            )
        )
    if "network_policies" in data:
        for key, rule_data in data["network_policies"].items():
            policy.network_policies[key].CopyFrom(_dict_to_network_rule(rule_data))
    return policy


def _dict_to_l7_query(query_data: dict) -> dict[str, sandbox_pb2.L7QueryMatcher]:
    """Convert a query matcher dict to protobuf L7QueryMatcher map.

    Args:
        query_data: Mapping of parameter names to matcher definitions
            with ``glob`` (str) and/or ``any`` (list[str]) keys.

    Returns:
        dict[str, sandbox_pb2.L7QueryMatcher]: Protobuf matcher map.
    """
    result: dict[str, sandbox_pb2.L7QueryMatcher] = {}
    for key, matcher in query_data.items():
        result[key] = sandbox_pb2.L7QueryMatcher(
            glob=matcher.get("glob", ""),
            **{"any": matcher.get("any", [])},
        )
    return result


def _dict_to_network_rule(data: dict) -> sandbox_pb2.NetworkPolicyRule:
    """Convert a network rule dict to protobuf.

    Args:
        data: Network rule definition with ``name``, ``endpoints``,
            and ``binaries`` keys.

    Returns:
        sandbox_pb2.NetworkPolicyRule: Populated protobuf message.
    """
    rule = sandbox_pb2.NetworkPolicyRule(name=data.get("name", ""))
    for ep_data in data.get("endpoints", []):
        ep = sandbox_pb2.NetworkEndpoint(
            host=ep_data.get("host", ""),
            port=ep_data.get("port", 0),
            protocol=ep_data.get("protocol", ""),
            tls=ep_data.get("tls", ""),
            enforcement=ep_data.get("enforcement", ""),
            access=ep_data.get("access", ""),
            allowed_ips=ep_data.get("allowed_ips", []),
            ports=ep_data.get("ports", []),
        )
        for rule_data in ep_data.get("rules", []):
            allow = rule_data.get("allow", {})
            l7_allow = sandbox_pb2.L7Allow(
                method=allow.get("method", ""),
                path=allow.get("path", ""),
                command=allow.get("command", ""),
            )
            if "query" in allow:
                for k, v in _dict_to_l7_query(allow["query"]).items():
                    l7_allow.query[k].CopyFrom(v)
            ep.rules.append(sandbox_pb2.L7Rule(allow=l7_allow))
        rule.endpoints.append(ep)
    for bin_data in data.get("binaries", []):
        rule.binaries.append(sandbox_pb2.NetworkBinary(path=bin_data.get("path", "")))
    return rule
