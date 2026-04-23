"""Unit tests for policy merge-operation synthesis (WS6b).

Covers :func:`shoreguard.services.policy_merge_ops.compute_merge_operations`
behaviour: empty diffs, pure adds, pure removes, modifications, ordering
invariants, unsupported-section rejection, and structural equality that
is order-insensitive on list members.
"""

from __future__ import annotations

import pytest

from shoreguard.services.policy_merge_ops import (
    UnsupportedMergeError,
    compute_merge_operations,
)


def _rule(
    name: str,
    endpoints: list[dict] | None = None,
    binaries: list[dict] | None = None,
) -> dict:
    return {
        "name": name,
        "endpoints": endpoints or [],
        "binaries": binaries or [],
    }


def _policy(rules: dict[str, dict]) -> dict:
    return {"network_policies": rules}


# ---------------------------------------------------------------------------
# Trivial cases
# ---------------------------------------------------------------------------


def test_empty_to_empty_is_no_op() -> None:
    assert compute_merge_operations({}, {}) == []


def test_equal_policies_produce_no_ops() -> None:
    p = _policy({"allow-gh": _rule("allow-gh", [{"host": "api.github.com", "port": 443}])})
    assert compute_merge_operations(p, p) == []


def test_equal_policies_with_empty_network_policies() -> None:
    assert compute_merge_operations(_policy({}), _policy({})) == []


# ---------------------------------------------------------------------------
# Pure adds and pure removes
# ---------------------------------------------------------------------------


def test_pure_add_emits_add_rule() -> None:
    current = _policy({})
    target = _policy({"allow-gh": _rule("allow-gh", [{"host": "api.github.com", "port": 443}])})
    ops = compute_merge_operations(current, target)
    assert ops == [
        {
            "type": "add_rule",
            "rule_name": "allow-gh",
            "rule": target["network_policies"]["allow-gh"],
        }
    ]


def test_pure_remove_emits_remove_rule() -> None:
    current = _policy({"old": _rule("old")})
    target = _policy({})
    ops = compute_merge_operations(current, target)
    assert ops == [{"type": "remove_rule", "rule_name": "old"}]


def test_multiple_adds_sorted_by_rule_name() -> None:
    current = _policy({})
    target = _policy(
        {
            "zeta": _rule("zeta"),
            "alpha": _rule("alpha"),
            "mu": _rule("mu"),
        }
    )
    ops = compute_merge_operations(current, target)
    names = [op["rule_name"] for op in ops]
    assert names == ["alpha", "mu", "zeta"]
    assert all(op["type"] == "add_rule" for op in ops)


# ---------------------------------------------------------------------------
# Modifications: rule body changed → remove_rule + add_rule
# ---------------------------------------------------------------------------


def test_modified_rule_emits_remove_then_add() -> None:
    current = _policy({"api": _rule("api", [{"host": "api.example.com", "port": 443}])})
    target = _policy({"api": _rule("api", [{"host": "api.example.com", "port": 8443}])})
    ops = compute_merge_operations(current, target)
    assert [op["type"] for op in ops] == ["remove_rule", "add_rule"]
    assert ops[0] == {"type": "remove_rule", "rule_name": "api"}
    assert ops[1]["rule_name"] == "api"
    assert ops[1]["rule"] == target["network_policies"]["api"]


def test_equal_rule_body_with_reordered_endpoints_no_op() -> None:
    """Endpoint ordering inside a rule is not semantically meaningful — a
    re-ordering must not register as a modification."""
    current = _policy(
        {
            "multi": _rule(
                "multi",
                [
                    {"host": "a.com", "port": 443},
                    {"host": "b.com", "port": 443},
                ],
            )
        }
    )
    target = _policy(
        {
            "multi": _rule(
                "multi",
                [
                    {"host": "b.com", "port": 443},
                    {"host": "a.com", "port": 443},
                ],
            )
        }
    )
    assert compute_merge_operations(current, target) == []


def test_equal_rule_body_with_reordered_binaries_no_op() -> None:
    current = _policy(
        {
            "tools": _rule(
                "tools",
                binaries=[{"path": "/usr/bin/curl"}, {"path": "/usr/bin/wget"}],
            )
        }
    )
    target = _policy(
        {
            "tools": _rule(
                "tools",
                binaries=[{"path": "/usr/bin/wget"}, {"path": "/usr/bin/curl"}],
            )
        }
    )
    assert compute_merge_operations(current, target) == []


# ---------------------------------------------------------------------------
# Ordering invariant: all removes before any add
# ---------------------------------------------------------------------------


def test_removes_precede_adds_in_mixed_diff() -> None:
    current = _policy({"gone": _rule("gone")})
    target = _policy({"new": _rule("new")})
    ops = compute_merge_operations(current, target)
    types = [op["type"] for op in ops]
    assert types == ["remove_rule", "add_rule"]


def test_modifications_interleave_correctly_with_pure_ops() -> None:
    """Pure removes first, then modification pairs (already remove-then-
    add), then pure adds. A mixed diff exercises all three groups."""
    current = _policy(
        {
            "to-remove": _rule("to-remove"),
            "to-modify": _rule("to-modify", [{"host": "old.example.com", "port": 443}]),
        }
    )
    target = _policy(
        {
            "to-modify": _rule("to-modify", [{"host": "new.example.com", "port": 443}]),
            "to-add": _rule("to-add"),
        }
    )
    ops = compute_merge_operations(current, target)
    assert [(op["type"], op.get("rule_name")) for op in ops] == [
        ("remove_rule", "to-remove"),
        ("remove_rule", "to-modify"),
        ("add_rule", "to-modify"),
        ("add_rule", "to-add"),
    ]


# ---------------------------------------------------------------------------
# Unsupported-section rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "current_value", "target_value"),
    [
        ("filesystem", {"read_only": ["/usr"]}, {"read_only": ["/etc"]}),
        ("process", {"run_as_user": "nobody"}, {"run_as_user": "root"}),
        ("landlock", {"compatibility": "best_effort"}, {"compatibility": "strict"}),
    ],
)
def test_diff_in_unsupported_section_raises(
    section: str, current_value: dict, target_value: dict
) -> None:
    current = {**_policy({}), section: current_value}
    target = {**_policy({}), section: target_value}
    with pytest.raises(UnsupportedMergeError, match=section):
        compute_merge_operations(current, target)


def test_equal_unsupported_sections_are_ignored() -> None:
    """filesystem/process/landlock that are identical on both sides do
    not prevent a network-only diff from computing."""
    fs = {"read_only": ["/usr"]}
    current = {**_policy({"r": _rule("r")}), "filesystem": fs}
    target = {**_policy({}), "filesystem": fs}
    ops = compute_merge_operations(current, target)
    assert ops == [{"type": "remove_rule", "rule_name": "r"}]


def test_explicit_version_bump_raises() -> None:
    current = {**_policy({}), "version": 3}
    target = {**_policy({}), "version": 4}
    with pytest.raises(UnsupportedMergeError, match="version bump"):
        compute_merge_operations(current, target)


def test_version_zero_mismatch_is_not_a_bump() -> None:
    """Round-tripped policies may lose the version field; an absent or
    zero version on either side is not a merge-relevant signal."""
    current = {**_policy({"r": _rule("r")}), "version": 0}
    target = _policy({"r": _rule("r")})
    assert compute_merge_operations(current, target) == []


# ---------------------------------------------------------------------------
# Property-style: applying the ops to current yields something that
# matches target on the network-policies axis
# ---------------------------------------------------------------------------


def _apply_ops_to_policy(current: dict, ops: list[dict]) -> dict:
    """Naive in-memory applier mirroring the gateway's rule-level
    semantics. Enough for property verification."""
    rules = dict(current.get("network_policies", {}) or {})
    for op in ops:
        if op["type"] == "remove_rule":
            rules.pop(op["rule_name"], None)
        elif op["type"] == "add_rule":
            rules[op["rule_name"]] = op["rule"]
        else:  # pragma: no cover - not emitted by compute_merge_operations today
            raise AssertionError(f"unexpected op: {op['type']}")
    new = dict(current)
    new["network_policies"] = rules
    return new


def test_applying_ops_transitions_current_to_target() -> None:
    current = _policy(
        {
            "stay": _rule("stay"),
            "modify": _rule("modify", [{"host": "old.com", "port": 80}]),
            "drop": _rule("drop"),
        }
    )
    target = _policy(
        {
            "stay": _rule("stay"),
            "modify": _rule("modify", [{"host": "new.com", "port": 443}]),
            "add": _rule("add", [{"host": "new-rule.com", "port": 443}]),
        }
    )
    ops = compute_merge_operations(current, target)
    result = _apply_ops_to_policy(current, ops)
    assert result.get("network_policies") == target["network_policies"]
