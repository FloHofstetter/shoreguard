"""Approval grant-narrowness heuristic (Policy Simulator, slice A).

Policy authoring is trial-and-error, and an approval chunk often proposes a
grant far broader than the single denial that prompted it (e.g. host ``**``
for one observed request). This pure helper ranks each pending chunk's
proposed-rule breadth and flags over-broad grants so the approval inbox can
badge them for tighter review. It is a breadth *heuristic*, not the upstream
server-side narrowness scorer ([OpenShell #1840]) — it never mutates a chunk
or auto-routes anything; it only annotates ``chunk["narrowness"]``.
"""

from __future__ import annotations

from typing import Any

# Host/path patterns that match essentially everything.
_BROAD_HOSTS = {"*", "**", "*.*"}
_BROAD_PATHS = {"*", "**", "/*", "/**", "/", ""}


def _is_broad_host(host: str) -> bool:
    """Return whether a host pattern grants effectively any host.

    Args:
        host: The proposed host pattern.

    Returns:
        bool: True when the pattern matches any host.
    """
    h = host.strip().lower()
    return h in _BROAD_HOSTS or "**" in h


def _is_broad_path(path: str) -> bool:
    """Return whether a path pattern grants effectively any path.

    Args:
        path: The proposed path pattern.

    Returns:
        bool: True when the pattern matches any path.
    """
    p = path.strip()
    return p in _BROAD_PATHS or p.rstrip("/") in ("", "/**", "**")


def _endpoint_paths(ep: dict[str, Any]) -> list[str]:
    """Collect proposed path patterns from an endpoint's L7 rules.

    Args:
        ep: A proposed-rule endpoint dict.

    Returns:
        list[str]: Path patterns referenced by the endpoint's rules.
    """
    paths: list[str] = []
    for rule in ep.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        allow = rule.get("allow") if isinstance(rule.get("allow"), dict) else rule
        value = allow.get("path") if isinstance(allow, dict) else None
        if isinstance(value, str):
            paths.append(value)
    for value in ep.get("paths") or []:
        if isinstance(value, str):
            paths.append(value)
    return paths


def assess_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    """Assess one approval chunk's proposed-rule breadth.

    Args:
        chunk: An approval chunk dict (with ``proposed_rule``).

    Returns:
        dict[str, Any]: ``{"verdict": "narrow"|"over_broad"|"unknown",
            "reasons": [...], "over_broad_fields": [...]}``.
    """
    rule = chunk.get("proposed_rule")
    if not isinstance(rule, dict):
        return {"verdict": "unknown", "reasons": [], "over_broad_fields": []}
    endpoints = rule.get("endpoints")
    if not endpoints:
        return {"verdict": "unknown", "reasons": [], "over_broad_fields": []}

    reasons: list[str] = []
    over_broad_fields: list[str] = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        host = str(ep.get("host") or "")
        if host and _is_broad_host(host):
            reasons.append(f"host '{host}' matches any host")
            over_broad_fields.append(f"host={host}")
        for path in _endpoint_paths(ep):
            if _is_broad_path(path):
                reasons.append(f"path '{path}' matches any path")
                over_broad_fields.append(f"path={path}")

    verdict = "over_broad" if over_broad_fields else "narrow"
    return {"verdict": verdict, "reasons": reasons, "over_broad_fields": over_broad_fields}


def annotate_narrowness(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate each chunk with a ``narrowness`` breadth assessment.

    Args:
        chunks: Approval chunk dicts (as enriched by the approvals service).

    Returns:
        list[dict[str, Any]]: The same list, with ``narrowness`` set on each.
    """
    for chunk in chunks:
        chunk["narrowness"] = assess_chunk(chunk)
    return chunks
