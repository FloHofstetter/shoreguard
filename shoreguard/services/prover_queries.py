"""Built-in verification query templates for the Z3 policy prover.

Each template defines a security property to check against an OpenShell
sandbox policy.  The prover encodes the policy as Z3 constraints and asks
whether a counterexample exists that satisfies the policy but violates the
desired property.  SAT = vulnerability found (with concrete counterexample),
UNSAT = property holds.
"""

from __future__ import annotations

from typing import Any

import z3  # pyright: ignore[reportMissingTypeStubs]

# Z3's type stubs are incomplete — Or/And/BoolVal return types are too broad.
# pyright: reportReturnType=false, reportArgumentType=false, reportAssignmentType=false

# ---------------------------------------------------------------------------
# Domain variables shared across queries
# ---------------------------------------------------------------------------


class NetVars:
    """Z3 variables for network-layer queries."""

    __slots__ = ("binary", "host", "port", "protocol", "method", "path", "rule_tag")

    def __init__(self) -> None:  # noqa: D107
        self.binary = z3.String("binary")
        self.host = z3.String("host")
        self.port = z3.Int("port")
        self.protocol = z3.String("protocol")
        self.method = z3.String("method")
        self.path = z3.String("path")
        self.rule_tag = z3.String("rule_tag")


class FsVars:
    """Z3 variables for filesystem-layer queries."""

    __slots__ = ("fs_path",)

    def __init__(self) -> None:  # noqa: D107
        self.fs_path = z3.String("fs_path")


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def encode_host_match(host_pattern: str, host_var: z3.SeqRef) -> z3.BoolRef:
    """Encode a host pattern into a Z3 constraint.

    Supports ``*.example.com`` (suffix match) and exact match.

    Args:
        host_pattern: Host string, optionally prefixed with ``*.``.
        host_var: Z3 string variable representing the target host.

    Returns:
        z3.BoolRef: Constraint that ``host_var`` matches the pattern.
    """
    if host_pattern.startswith("*."):
        suffix = host_pattern[1:]  # ".example.com"
        bare = host_pattern[2:]  # "example.com"
        return z3.Or(
            z3.SuffixOf(z3.StringVal(suffix), host_var),
            host_var == z3.StringVal(bare),
        )
    return host_var == z3.StringVal(host_pattern)


def encode_path_match(path_pattern: str, path_var: z3.SeqRef) -> z3.BoolRef:
    """Encode a path pattern into a Z3 constraint.

    ``/**`` or ``/*`` at the end maps to prefix match.  Otherwise exact match.

    Args:
        path_pattern: URL path pattern (may end with ``/**`` or ``/*``).
        path_var: Z3 string variable representing the request path.

    Returns:
        z3.BoolRef: Constraint that ``path_var`` matches the pattern.
    """
    if path_pattern.endswith("/**"):
        prefix = path_pattern[:-2]  # "/foo" from "/foo/**"
        return z3.Or(
            z3.PrefixOf(z3.StringVal(prefix + "/"), path_var),
            path_var == z3.StringVal(prefix),
        )
    if path_pattern.endswith("/*"):
        prefix = path_pattern[:-1]  # "/foo/" from "/foo/*"
        return z3.And(
            z3.PrefixOf(z3.StringVal(prefix), path_var),
            # No further '/' after the prefix (single segment)
            z3.Not(
                z3.Contains(
                    z3.SubString(path_var, z3.Length(z3.StringVal(prefix)), z3.Length(path_var)),
                    z3.StringVal("/"),
                )
            ),
        )
    return path_var == z3.StringVal(path_pattern)


def encode_network_policy(policy: dict[str, Any], v: NetVars) -> z3.BoolRef:
    """Encode the full network policy as a disjunction of allow clauses.

    Each network rule + endpoint pair becomes one allow clause.  Traffic is
    permitted if ANY clause matches.

    Args:
        policy: Policy dict containing ``network_policies``.
        v: Z3 variables for the network domain.

    Returns:
        z3.BoolRef: ``z3.BoolVal(False)`` for an empty policy, otherwise
            the disjunction of all allow clauses.
    """
    net_rules = policy.get("network_policies", {})
    if not net_rules:
        return z3.BoolVal(False)

    allow_clauses: list[z3.BoolRef] = []
    for rule_key, rule in net_rules.items():
        binaries = rule.get("binaries", [])
        for ep in rule.get("endpoints", []):
            clause = _encode_endpoint(rule_key, ep, binaries, v)
            if clause is not None:
                allow_clauses.append(clause)

    if not allow_clauses:
        return z3.BoolVal(False)
    return z3.Or(*allow_clauses) if len(allow_clauses) > 1 else allow_clauses[0]


def _encode_endpoint(
    rule_key: str,
    ep: dict[str, Any],
    binaries: list[dict[str, Any]],
    v: NetVars,
) -> z3.BoolRef | None:
    """Encode a single endpoint within a network rule.

    Args:
        rule_key: Rule identifier for counterexample attribution.
        ep: Endpoint dict with host, port, protocol, rules, etc.
        binaries: Binary restriction list from the parent rule.
        v: Z3 variables for the network domain.

    Returns:
        z3.BoolRef | None: Conjunction of endpoint constraints, or ``None``
            if the endpoint produces no constraints.
    """
    parts: list[z3.BoolRef] = []

    # Rule tag tracking (for counterexample attribution)
    parts.append(v.rule_tag == z3.StringVal(rule_key))

    # Host
    host = ep.get("host", "")
    if host:
        parts.append(encode_host_match(host, v.host))

    # Port
    port = ep.get("port")
    if port:
        parts.append(v.port == port)
    extra_ports = ep.get("ports", [])
    if extra_ports:
        port_clauses = [v.port == p for p in extra_ports]
        if port:
            port_clauses.append(v.port == port)
            # Remove the standalone port constraint added above
            parts = [p for p in parts if not (z3.is_expr(p) and z3.eq(p, v.port == port))]
        parts.append(z3.Or(*port_clauses) if len(port_clauses) > 1 else port_clauses[0])

    # Protocol
    proto = ep.get("protocol", "")
    if proto:
        parts.append(v.protocol == z3.StringVal(proto))

    # Binary restriction
    if binaries:
        bin_paths = [b.get("path", "") for b in binaries if b.get("path")]
        if bin_paths:
            bin_clauses = [v.binary == z3.StringVal(p) for p in bin_paths]
            parts.append(z3.Or(*bin_clauses) if len(bin_clauses) > 1 else bin_clauses[0])

    # L7 allow rules
    l7_rules = ep.get("rules", [])
    if l7_rules:
        l7_clauses: list[z3.BoolRef] = []
        for r in l7_rules:
            allow = r.get("allow", {})
            clause = _encode_l7_match(allow, v)
            if clause is not None:
                l7_clauses.append(clause)
        if l7_clauses:
            parts.append(z3.Or(*l7_clauses) if len(l7_clauses) > 1 else l7_clauses[0])

    # L7 deny rules take precedence: a request that matches any deny clause
    # is blocked even if it also matches an allow clause. We encode this by
    # AND-ing the endpoint with NOT(any_deny_matches).
    deny_rules = ep.get("deny_rules", [])
    if deny_rules:
        deny_clauses: list[z3.BoolRef] = []
        for d in deny_rules:
            clause = _encode_l7_match(d, v)
            if clause is not None:
                deny_clauses.append(clause)
        if deny_clauses:
            deny_any = z3.Or(*deny_clauses) if len(deny_clauses) > 1 else deny_clauses[0]
            parts.append(z3.Not(deny_any))

    if not parts:
        return None
    return z3.And(*parts) if len(parts) > 1 else parts[0]


def _encode_l7_match(rule: dict[str, Any], v: NetVars) -> z3.BoolRef | None:
    """Encode an L7 rule (allow or deny) into a Z3 constraint.

    Both ``L7Allow`` and ``L7DenyRule`` share the same matcher fields
    (method, path, command) so this helper is reused for the allow and
    deny encoding paths.

    Args:
        rule: L7 rule dict with ``method`` / ``path`` fields.
        v: Z3 network variables.

    Returns:
        z3.BoolRef | None: Conjunction of matcher constraints, or
            ``None`` if the rule has no recognised fields.
    """
    l7_parts: list[z3.BoolRef] = []
    method = rule.get("method", "")
    if method:
        l7_parts.append(v.method == z3.StringVal(method))
    path = rule.get("path", "")
    if path:
        l7_parts.append(encode_path_match(path, v.path))
    if not l7_parts:
        return None
    return z3.And(*l7_parts) if len(l7_parts) > 1 else l7_parts[0]


def encode_filesystem_policy(policy: dict[str, Any], v: FsVars) -> tuple[z3.BoolRef, z3.BoolRef]:
    """Encode filesystem policy into (read_allowed, write_allowed) constraints.

    Args:
        policy: Policy dict containing ``filesystem``.
        v: Z3 variables for the filesystem domain.

    Returns:
        tuple[z3.BoolRef, z3.BoolRef]: ``(read_allowed, write_allowed)``
            constraints.
    """
    fs = policy.get("filesystem", {})
    ro_paths = fs.get("read_only", [])
    rw_paths = fs.get("read_write", [])
    include_workdir = fs.get("include_workdir", False)

    write_clauses: list[z3.BoolRef] = []
    for p in rw_paths:
        write_clauses.append(encode_path_match(p, v.fs_path))
    if include_workdir:
        write_clauses.append(z3.PrefixOf(z3.StringVal("/workdir"), v.fs_path))

    write_allowed = (
        z3.Or(*write_clauses)
        if len(write_clauses) > 1
        else write_clauses[0]
        if write_clauses
        else z3.BoolVal(False)
    )

    read_clauses: list[z3.BoolRef] = [write_allowed]
    for p in ro_paths:
        read_clauses.append(encode_path_match(p, v.fs_path))

    read_allowed = z3.Or(*read_clauses) if len(read_clauses) > 1 else read_clauses[0]

    return read_allowed, write_allowed


# ---------------------------------------------------------------------------
# Query implementations
# ---------------------------------------------------------------------------


def _can_exfiltrate(
    policy: dict[str, Any],
    params: dict[str, Any],
    v: NetVars,
) -> tuple[z3.BoolRef, str]:
    """Check if data can flow to a given host pattern.

    Args:
        policy: Policy dict.
        params: Must contain ``host_pattern``.
        v: Z3 network variables.

    Returns:
        tuple[z3.BoolRef, str]: Z3 formula and human-readable description.

    Raises:
        ValueError: If ``host_pattern`` is missing.
    """
    host_pattern = params.get("host_pattern", "")
    if not host_pattern:
        msg = "host_pattern parameter is required"
        raise ValueError(msg)

    policy_allows = encode_network_policy(policy, v)
    target_match = encode_host_match(host_pattern, v.host)

    return z3.And(policy_allows, target_match), f"Can data be exfiltrated to {host_pattern}?"


def _unrestricted_egress(
    policy: dict[str, Any],
    params: dict[str, Any],
    v: NetVars,
) -> tuple[z3.BoolRef, str]:
    """Check if there is unrestricted outbound network access.

    Asks: can traffic reach a host that is NOT in the explicit allow-list?

    Args:
        policy: Policy dict.
        params: Unused (no parameters required).
        v: Z3 network variables.

    Returns:
        tuple[z3.BoolRef, str]: Z3 formula and human-readable description.
    """
    net_rules = policy.get("network_policies", {})
    policy_allows = encode_network_policy(policy, v)

    # Collect all explicitly allowed hosts
    explicit_hosts: set[str] = set()
    for rule in net_rules.values():
        for ep in rule.get("endpoints", []):
            host = ep.get("host", "")
            if host and not host.startswith("*"):
                explicit_hosts.add(host)

    if not explicit_hosts:
        # No explicit hosts means either wildcard or empty policy
        # If policy allows anything, it's unrestricted
        return policy_allows, "Is there any unrestricted egress?"

    # Can traffic reach a host NOT in the explicit list?
    not_in_list = z3.And(*[v.host != z3.StringVal(h) for h in explicit_hosts])

    return (
        z3.And(policy_allows, not_in_list),
        "Is there unrestricted egress (traffic to hosts outside the explicit allow-list)?",
    )


def _binary_bypass(
    policy: dict[str, Any],
    params: dict[str, Any],
    v: NetVars,
) -> tuple[z3.BoolRef, str]:
    """Check if a specific binary can access endpoints via unrestricted rules.

    A rule with an empty ``binaries`` list allows any binary.  This query
    checks whether the given binary can use such unrestricted rules.

    Args:
        policy: Policy dict.
        params: Must contain ``binary_path``.
        v: Z3 network variables.

    Returns:
        tuple[z3.BoolRef, str]: Z3 formula and human-readable description.

    Raises:
        ValueError: If ``binary_path`` is missing.
    """
    binary_path = params.get("binary_path", "")
    if not binary_path:
        msg = "binary_path parameter is required"
        raise ValueError(msg)

    net_rules = policy.get("network_policies", {})
    unrestricted_clauses: list[z3.BoolRef] = []
    for rule_key, rule in net_rules.items():
        binaries = rule.get("binaries", [])
        if binaries:
            continue  # This rule restricts to specific binaries — skip
        for ep in rule.get("endpoints", []):
            clause = _encode_endpoint(rule_key, ep, [], v)
            if clause is not None:
                unrestricted_clauses.append(clause)

    if not unrestricted_clauses:
        return z3.BoolVal(False), f"Can binary {binary_path} bypass policy via unrestricted rules?"

    unrestricted_allows = (
        z3.Or(*unrestricted_clauses) if len(unrestricted_clauses) > 1 else unrestricted_clauses[0]
    )

    return (
        z3.And(v.binary == z3.StringVal(binary_path), unrestricted_allows),
        f"Can binary {binary_path} bypass policy via unrestricted rules?",
    )


def _write_despite_readonly(
    policy: dict[str, Any],
    params: dict[str, Any],
    v_unused: NetVars,
) -> tuple[z3.BoolRef, str]:
    """Check if writes can occur to paths that are only listed as read-only.

    Uses filesystem variables instead of network variables.

    Args:
        policy: Policy dict.
        params: Unused (no parameters required).
        v_unused: Unused network variables (filesystem vars created internally).

    Returns:
        tuple[z3.BoolRef, str]: Z3 formula and human-readable description.
    """
    fsv = FsVars()
    read_allowed, write_allowed = encode_filesystem_policy(policy, fsv)

    # Is there a path in the read_only set that is ALSO writable?
    fs = policy.get("filesystem", {})
    ro_paths = fs.get("read_only", [])

    if not ro_paths:
        return z3.BoolVal(False), "Can writes occur to read-only paths?"

    ro_match_clauses = [encode_path_match(p, fsv.fs_path) for p in ro_paths]
    ro_match = z3.Or(*ro_match_clauses) if len(ro_match_clauses) > 1 else ro_match_clauses[0]

    return (
        z3.And(ro_match, write_allowed),
        "Can writes occur to paths marked as read-only?",
    )


# ---------------------------------------------------------------------------
# Preset registry
# ---------------------------------------------------------------------------

PRESET_QUERIES: dict[str, dict[str, Any]] = {
    "can_exfiltrate": {
        "label": "Data exfiltration check",
        "description": "Can any binary send data to a specified host pattern?",
        "params": {
            "host_pattern": {
                "type": "string",
                "required": True,
                "placeholder": "*.evil.com",
            },
        },
        "fn": _can_exfiltrate,
    },
    "unrestricted_egress": {
        "label": "Unrestricted egress check",
        "description": "Is there any unrestricted outbound network access?",
        "params": {},
        "fn": _unrestricted_egress,
    },
    "binary_bypass": {
        "label": "Binary bypass check",
        "description": "Can a specific binary bypass its policy restrictions?",
        "params": {
            "binary_path": {
                "type": "string",
                "required": True,
                "placeholder": "/usr/bin/curl",
            },
        },
        "fn": _binary_bypass,
    },
    "write_despite_readonly": {
        "label": "Read-only violation check",
        "description": "Can writes occur to paths marked as read-only?",
        "params": {},
        "fn": _write_despite_readonly,
    },
}
