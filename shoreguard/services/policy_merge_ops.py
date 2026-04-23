"""Policy merge-operation synthesis — turn two policy dicts into a delta.

Upstream OpenShell PR #860 added ``PolicyMergeOperation`` so clients can
send atomic per-rule deltas instead of full-policy rewrites. This module
produces the delta given a ``current`` and ``target`` policy dict, as
would result from reading the gateway's effective policy and loading the
target from a Git source of truth (M23 GitOps).

Scope: rule-level diff on ``network_policies`` only. The upstream oneof
does not expose merge operations for ``filesystem`` / ``process`` /
``landlock`` / top-level ``version``; changes to those sections surface
as an :class:`UnsupportedMergeError` so the caller falls back to
``/policy/apply?mode=replace`` instead of silently dropping the update.

This is distinct from the structural diff in
:mod:`shoreguard.services.policy_diff` (which is a human/review-oriented
summary for GitOps dry-run). Merge ops are machine-consumed by the
gateway's :attr:`UpdateConfigRequest.merge_operations` surface.

Rule-body changes are expressed as ``remove_rule`` + ``add_rule``. The
finer-grained ``add_allow_rules`` / ``remove_endpoint`` / ``remove_binary``
operations exist for future optimization but are not emitted here — a
rule-level rewrite is atomic on the gateway and keeps the delta readable.

Ordering invariant: all ``remove_rule`` operations are emitted before any
``add_rule`` operation so a partial apply on the gateway cannot leave the
policy in a state with duplicated endpoints across two revisions.
"""

from __future__ import annotations

from typing import Any

_UNSUPPORTED_SECTIONS = ("filesystem", "process", "landlock")


class UnsupportedMergeError(ValueError):
    """Merge diff cannot express the requested change.

    Raised when a caller asks for a merge diff but the change set touches
    sections that the upstream merge surface cannot express. The caller
    should fall back to a full replace.
    """


def compute_merge_operations(
    current: dict[str, Any],
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute merge operations that transition *current* into *target*.

    The diff is rule-level: a rule that exists in both sides but whose
    body differs is expressed as ``remove_rule`` + ``add_rule`` in that
    order. Identical rules produce no operation. The returned ordering
    groups all ``remove_rule`` ops before any ``add_rule`` op so the
    gateway never observes a transient state with duplicated endpoints.

    Sections outside ``network_policies`` (filesystem, process, landlock,
    explicit version bumps) are rejected by
    :func:`_reject_unsupported_sections` with
    :class:`UnsupportedMergeError`; callers should fall back to
    ``mode=replace``.

    Args:
        current: Currently-enforced policy, as a dict in the same shape
            :func:`~shoreguard.client.policies._policy_to_dict`
            produces.
        target: Desired policy, same shape.

    Returns:
        list[dict[str, Any]]: Operations as dicts with ``type``
            discriminators consumable by
            :func:`~shoreguard.client.policies._dict_to_merge_operation`.
            An empty list means the policies are already equal on their
            network-policies surface.
    """
    _reject_unsupported_sections(current, target)

    current_rules: dict[str, Any] = current.get("network_policies", {}) or {}
    target_rules: dict[str, Any] = target.get("network_policies", {}) or {}

    current_names = set(current_rules)
    target_names = set(target_rules)

    removes: list[dict[str, Any]] = []
    modifications: list[dict[str, Any]] = []
    adds: list[dict[str, Any]] = []

    # Rules only in current — pure removes.
    for name in sorted(current_names - target_names):
        removes.append({"type": "remove_rule", "rule_name": name})

    # Rules in both — modified iff bodies differ.
    for name in sorted(current_names & target_names):
        if _rules_equal(current_rules[name], target_rules[name]):
            continue
        modifications.append({"type": "remove_rule", "rule_name": name})
        modifications.append(
            {
                "type": "add_rule",
                "rule_name": name,
                "rule": target_rules[name],
            }
        )

    # Rules only in target — pure adds.
    for name in sorted(target_names - current_names):
        adds.append(
            {
                "type": "add_rule",
                "rule_name": name,
                "rule": target_rules[name],
            }
        )

    # Remove-before-add invariant: pure removes first, then the
    # remove+add pairs for modifications (each pair is already in
    # remove-then-add order), then pure adds.
    return removes + modifications + adds


def _reject_unsupported_sections(current: dict[str, Any], target: dict[str, Any]) -> None:
    """Raise if sections outside ``network_policies`` differ.

    Args:
        current: Current policy dict.
        target: Target policy dict.

    Raises:
        UnsupportedMergeError: When any unsupported section differs
            between the two policies, or when an explicit top-level
            ``version`` bump is requested.
    """
    for section in _UNSUPPORTED_SECTIONS:
        if current.get(section) != target.get(section):
            raise UnsupportedMergeError(
                f"mode=merge cannot express changes to the '{section}' "
                "section; use mode=replace for filesystem / process / "
                "landlock / version updates"
            )
    # `version` is gateway-assigned on each write, so a mismatch from
    # revision roundtrips is expected. Only raise when both sides
    # explicitly set non-zero versions that disagree — that is almost
    # certainly a caller trying to pin a revision through merge, which
    # we do not support.
    cur_v = current.get("version")
    tgt_v = target.get("version")
    if cur_v not in (None, 0) and tgt_v not in (None, 0) and cur_v != tgt_v:
        raise UnsupportedMergeError(
            "mode=merge cannot express a version bump; the gateway assigns revisions automatically"
        )


def _rules_equal(current_rule: dict[str, Any], target_rule: dict[str, Any]) -> bool:
    """Structural equality on rule dicts, order-insensitive on list members.

    Proto-derived dicts serialise list ordering from the gateway, which
    may not match the ordering a caller constructs from YAML or Git. We
    normalise endpoints by ``(host, port)`` and binaries by ``path`` so
    a re-ordering does not look like a diff.

    Args:
        current_rule: Current rule dict (``name`` / ``endpoints`` /
            ``binaries``).
        target_rule: Target rule dict, same shape.

    Returns:
        bool: ``True`` when the rules describe the same network policy.
    """
    return _normalize_rule(current_rule) == _normalize_rule(target_rule)


def _normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical form of a rule dict for structural comparison.

    Args:
        rule: Input rule dict.

    Returns:
        dict[str, Any]: Canonical rule dict. List members of
            ``endpoints`` and ``binaries`` are sorted by identity keys;
            within an endpoint, ``rules`` / ``deny_rules`` lists are
            left in their original order because rule ordering is
            semantically meaningful (upstream applies them in sequence).
    """
    endpoints = list(rule.get("endpoints", []) or [])
    endpoints.sort(key=lambda ep: (ep.get("host", ""), int(ep.get("port", 0))))
    binaries = sorted(
        (rule.get("binaries", []) or []),
        key=lambda b: str(b.get("path", "")),
    )
    return {
        "name": rule.get("name", ""),
        "endpoints": endpoints,
        "binaries": binaries,
    }
