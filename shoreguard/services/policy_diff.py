"""Structural diff between two sandbox policy dicts.

Returns a typed summary describing which fields changed, rather than
a raw text diff. This keeps the GitOps export/apply flow's
``dry_run`` response machine-readable and stable across YAML
formatting changes: two policies that serialise to different
whitespace but carry the same semantic content produce an empty
diff.
"""

from __future__ import annotations

from typing import Any, TypedDict


class FilesystemDiff(TypedDict, total=False):
    """Per-section filesystem diff (added/removed paths + workdir toggle).

    Attributes:
        read_only_added: Paths newly listed under read_only.
        read_only_removed: Paths removed from read_only.
        read_write_added: Paths newly listed under read_write.
        read_write_removed: Paths removed from read_write.
        include_workdir_changed: ``(old, new)`` tuple if include_workdir flipped.
    """

    read_only_added: list[str]
    read_only_removed: list[str]
    read_write_added: list[str]
    read_write_removed: list[str]
    include_workdir_changed: tuple[bool, bool]


class ProcessDiff(TypedDict, total=False):
    """Process-policy diff with old/new tuples for changed fields.

    Attributes:
        run_as_user_changed: ``(old, new)`` if run_as_user differs.
        run_as_group_changed: ``(old, new)`` if run_as_group differs.
    """

    run_as_user_changed: tuple[str, str]
    run_as_group_changed: tuple[str, str]


class NetworkPoliciesDiff(TypedDict):
    """Network rule diff: added / removed / changed rule keys.

    Attributes:
        added: Rule keys present in new but not old.
        removed: Rule keys present in old but not new.
        changed: Rule keys whose contents differ.
    """

    added: list[str]
    removed: list[str]
    changed: list[str]


class PolicyDiff(TypedDict):
    """Top-level structural diff returned by ``diff_policy``.

    Attributes:
        filesystem: Filesystem section diff.
        process: Process section diff.
        network_policies: Network policies section diff.
    """

    filesystem: FilesystemDiff
    process: ProcessDiff
    network_policies: NetworkPoliciesDiff


def diff_policy(old: dict[str, Any] | None, new: dict[str, Any] | None) -> PolicyDiff:
    """Return a structured diff of two policy dicts.

    Args:
        old: Previous policy dict (as produced by ``_policy_to_dict``).
        new: New policy dict.

    Returns:
        PolicyDiff: Per-section additions / removals / changes.
    """
    old = old or {}
    new = new or {}

    fs_diff: FilesystemDiff = {}
    old_fs = old.get("filesystem") or {}
    new_fs = new.get("filesystem") or {}
    old_ro = set(old_fs.get("read_only", []))
    new_ro = set(new_fs.get("read_only", []))
    if added := sorted(new_ro - old_ro):
        fs_diff["read_only_added"] = added
    if removed := sorted(old_ro - new_ro):
        fs_diff["read_only_removed"] = removed
    old_rw = set(old_fs.get("read_write", []))
    new_rw = set(new_fs.get("read_write", []))
    if added := sorted(new_rw - old_rw):
        fs_diff["read_write_added"] = added
    if removed := sorted(old_rw - new_rw):
        fs_diff["read_write_removed"] = removed
    old_iw = bool(old_fs.get("include_workdir", False))
    new_iw = bool(new_fs.get("include_workdir", False))
    if old_iw != new_iw:
        fs_diff["include_workdir_changed"] = (old_iw, new_iw)

    proc_diff: ProcessDiff = {}
    old_p = old.get("process") or {}
    new_p = new.get("process") or {}
    if old_p.get("run_as_user", "") != new_p.get("run_as_user", ""):
        proc_diff["run_as_user_changed"] = (
            old_p.get("run_as_user", ""),
            new_p.get("run_as_user", ""),
        )
    if old_p.get("run_as_group", "") != new_p.get("run_as_group", ""):
        proc_diff["run_as_group_changed"] = (
            old_p.get("run_as_group", ""),
            new_p.get("run_as_group", ""),
        )

    old_np = old.get("network_policies") or {}
    new_np = new.get("network_policies") or {}
    np_added = sorted(set(new_np) - set(old_np))
    np_removed = sorted(set(old_np) - set(new_np))
    np_changed = sorted(k for k in (set(old_np) & set(new_np)) if old_np[k] != new_np[k])
    np_diff: NetworkPoliciesDiff = {
        "added": np_added,
        "removed": np_removed,
        "changed": np_changed,
    }

    return {
        "filesystem": fs_diff,
        "process": proc_diff,
        "network_policies": np_diff,
    }


def is_empty(diff: PolicyDiff) -> bool:
    """Return True if the diff has no changes in any section.

    Args:
        diff: Diff produced by ``diff_policy``.

    Returns:
        bool: True if every section is empty.
    """
    if diff["filesystem"]:
        return False
    if diff["process"]:
        return False
    np = diff["network_policies"]
    return not (np["added"] or np["removed"] or np["changed"])


def summary(diff: PolicyDiff) -> dict[str, int]:
    """Return a small ``{section: change_count}`` summary for audit logs.

    Args:
        diff: Diff produced by ``diff_policy``.

    Returns:
        dict[str, int]: Per-section + total change count.
    """
    fs = diff["filesystem"]
    fs_count = sum(
        len(fs.get(k, []))  # type: ignore[arg-type]
        for k in ("read_only_added", "read_only_removed", "read_write_added", "read_write_removed")
    ) + (1 if "include_workdir_changed" in fs else 0)
    proc_count = len(diff["process"])
    np = diff["network_policies"]
    np_count = len(np["added"]) + len(np["removed"]) + len(np["changed"])
    return {
        "filesystem": fs_count,
        "process": proc_count,
        "network_policies": np_count,
        "total": fs_count + proc_count + np_count,
    }
