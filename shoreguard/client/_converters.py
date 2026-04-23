"""Protobuf ↔ dict conversion helpers shared across client managers."""

from __future__ import annotations

import logging
import os

from shoreguard.exceptions import ValidationError

from ._proto import sandbox_pb2

logger = logging.getLogger(__name__)


class PolicyValidationError(ValidationError):
    """Raised when a policy dict contains a disallowed pattern.

    Used to surface issues like TLD-level wildcards (``*.com``) that pass
    schema validation but would grant overly broad matches at enforcement
    time. Inherits from :class:`shoreguard.exceptions.ValidationError` so
    the FastAPI error handler translates it into HTTP 400 without any
    per-route plumbing.
    """


def _validate_host_pattern(host: str) -> None:
    """Reject host patterns that wildcard at the TLD level.

    A pattern is considered a TLD-wildcard if it begins with ``*.`` and the
    remainder contains no further dot — e.g. ``*.com``, ``*.io``. Such
    patterns match any second-level domain under a TLD and are almost
    always a misconfiguration rather than an intentional allowance.

    ``*.example.com`` is still accepted because the suffix (``example.com``)
    is itself multi-label.

    Args:
        host: Host pattern from a NetworkEndpoint.

    Raises:
        PolicyValidationError: If the pattern is a TLD-level wildcard.
    """
    if not host or not host.startswith("*."):
        return
    suffix = host[2:]
    if "." not in suffix:
        msg = (
            f"host pattern {host!r} wildcards at the TLD level; use a more "
            f"specific suffix like '*.example.{suffix}'"
        )
        raise PolicyValidationError(msg)


def _resolve_binary_path(path: str) -> str:
    """Resolve a policy binary path through any local symlinks.

    Upstream OpenShell #774 resolves symlinks at match time in the sandbox
    supervisor. ShoreGuard mirrors this defensively at policy-write time:
    if the path exists on the control-plane host and points through a
    symlink, the resolved target is persisted instead of the symlink. This
    closes the bypass where a policy allows ``/usr/bin/python`` but the
    same inode is reachable via ``/usr/local/bin/python`` (or vice versa).

    If the path does not exist locally — which is the common case for
    remote gateways — the original path is passed through unchanged.

    Args:
        path: Policy binary path as declared by the caller.

    Returns:
        str: Resolved path if a local symlink was followed, otherwise
            the original path.
    """
    if not path:
        return path
    try:
        if os.path.islink(path):
            resolved = os.path.realpath(path)
            if resolved != path:
                logger.warning(
                    "policy binary path %s is a symlink; resolved to %s",
                    path,
                    resolved,
                )
                return resolved
    except OSError as exc:
        logger.warning("could not stat policy binary path %s: %s", path, exc)
    return path


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


def _dict_to_l7_allow(allow: dict) -> sandbox_pb2.L7Allow:
    """Convert an L7 allow dict to a protobuf ``L7Allow`` message.

    Args:
        allow: Allow rule fields (method, path, command, query).

    Returns:
        sandbox_pb2.L7Allow: Populated protobuf message.
    """
    l7_allow = sandbox_pb2.L7Allow(
        method=allow.get("method", ""),
        path=allow.get("path", ""),
        command=allow.get("command", ""),
    )
    if "query" in allow:
        for k, v in _dict_to_l7_query(allow["query"]).items():
            l7_allow.query[k].CopyFrom(v)
    return l7_allow


def _dict_to_l7_deny(deny: dict) -> sandbox_pb2.L7DenyRule:
    """Convert an L7 deny dict to a protobuf ``L7DenyRule`` message.

    Deny rules share the same shape as allow rules (method, path,
    command, query) but semantically block matching requests even when
    an allow clause would accept them. Upstream OpenShell #822 defines
    deny rules as taking precedence over allow rules.

    Args:
        deny: Deny rule fields (method, path, command, query).

    Returns:
        sandbox_pb2.L7DenyRule: Populated protobuf message.
    """
    deny_rule = sandbox_pb2.L7DenyRule(
        method=deny.get("method", ""),
        path=deny.get("path", ""),
        command=deny.get("command", ""),
    )
    if "query" in deny:
        for k, v in _dict_to_l7_query(deny["query"]).items():
            deny_rule.query[k].CopyFrom(v)
    return deny_rule


def _dict_to_network_rule(data: dict) -> sandbox_pb2.NetworkPolicyRule:
    """Convert a network rule dict to protobuf.

    Validates host patterns against TLD-level wildcards (raises
    :class:`PolicyValidationError` transitively via
    :func:`_validate_host_pattern`) and resolves any local symlinks in
    binary paths before serialising.

    Args:
        data: Network rule definition with ``name``, ``endpoints``,
            and ``binaries`` keys.

    Returns:
        sandbox_pb2.NetworkPolicyRule: Populated protobuf message.
    """
    rule = sandbox_pb2.NetworkPolicyRule(name=data.get("name", ""))
    for ep_data in data.get("endpoints", []):
        host = ep_data.get("host", "")
        _validate_host_pattern(host)
        ep = sandbox_pb2.NetworkEndpoint(
            host=host,
            port=ep_data.get("port", 0),
            protocol=ep_data.get("protocol", ""),
            tls=ep_data.get("tls", ""),
            enforcement=ep_data.get("enforcement", ""),
            access=ep_data.get("access", ""),
            allowed_ips=ep_data.get("allowed_ips", []),
            ports=ep_data.get("ports", []),
            allow_encoded_slash=ep_data.get("allow_encoded_slash", False),
        )
        for rule_data in ep_data.get("rules", []):
            allow = rule_data.get("allow", {})
            ep.rules.append(sandbox_pb2.L7Rule(allow=_dict_to_l7_allow(allow)))
        for deny_data in ep_data.get("deny_rules", []):
            ep.deny_rules.append(_dict_to_l7_deny(deny_data))
        rule.endpoints.append(ep)
    for bin_data in data.get("binaries", []):
        original = bin_data.get("path", "")
        resolved = _resolve_binary_path(original)
        rule.binaries.append(sandbox_pb2.NetworkBinary(path=resolved))
    return rule
