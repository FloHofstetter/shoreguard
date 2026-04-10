"""OCSF shorthand log parser.

OpenShell v0.0.26 emits security events in a compact shorthand format over
the existing ``SandboxLogLine`` gRPC stream. These lines carry ``level="OCSF"``
(or ``target="ocsf"``) and a message like::

    NET:OPEN [INFO] ALLOWED /usr/bin/curl(58) -> api.github.com:443 [policy:github_api engine:opa]

This module turns those lines into normalized dicts so the API layer and the
frontend log viewer can filter, colour and expand OCSF events without doing
string inspection themselves.

Pure functions, no state, no I/O — mirror of ``shoreguard.services.formatters``.
"""

from __future__ import annotations

import re
from typing import Any

# Upstream class prefixes emitted by openshell-ocsf/src/format/shorthand.rs
# (v0.0.26). ``EVENT`` is the generic fallback class without a suffix.
_CLASS_PREFIXES: frozenset[str] = frozenset(
    {"NET", "HTTP", "SSH", "PROC", "FINDING", "CONFIG", "LIFECYCLE", "EVENT"}
)

# Severity tokens, mapped from OCSF severity_id 1..6 in upstream.
_SEVERITIES: frozenset[str] = frozenset({"INFO", "LOW", "MED", "HIGH", "CRIT", "FATAL"})

# Disposition tokens shown for connection-oriented classes (NET/HTTP/SSH).
_DISPOSITIONS: frozenset[str] = frozenset({"ALLOWED", "DENIED", "BLOCKED"})

# ``<PREFIX>:<ACTIVITY_OR_STATE>`` — the first whitespace-delimited token of a
# standard OCSF shorthand line. FINDING uses ``FINDING:<DISPOSITION>`` and
# CONFIG uses ``CONFIG:<STATE>`` — both match the same regex, they are split
# out at interpretation time.
_HEAD_RE = re.compile(r"^(?P<prefix>[A-Z]+)(?::(?P<suffix>[A-Z_]+))?\s*(?P<rest>.*)$")

# ``[SEVERITY]`` immediately after the head.
_SEVERITY_RE = re.compile(r"^\[(?P<severity>[A-Z]+)\]\s*(?P<rest>.*)$")

# Trailing ``[key:value key:value ...]`` bracket. Anchored to the end of the
# string so it does not eat inline brackets in the summary (e.g. IPv6).
_TRAILING_BRACKET_RE = re.compile(r"\s*\[(?P<body>[^\[\]]*)\]\s*$")

# Process-binary pattern: ``/absolute/path(pid)`` as seen at the head of
# NET/HTTP/SSH/PROC summaries, e.g. ``/usr/bin/curl(58) -> api.github.com:443``.
_BINARY_RE = re.compile(r"(?P<binary>/[^\s()]+)\(\d+\)")


def _is_ocsf(log: dict[str, Any]) -> bool:
    """Return whether *log* looks like an OCSF shorthand entry.

    Args:
        log: A flattened sandbox log line dict with ``level`` and ``target``
            fields (shape produced by ``SandboxManager.get_logs`` / ``watch``).

    Returns:
        bool: ``True`` if the log's level is ``OCSF`` or its target is
        ``ocsf``, else ``False``.
    """
    level = str(log.get("level") or "").upper()
    target = str(log.get("target") or "").lower()
    return level == "OCSF" or target == "ocsf"


def _split_trailing_bracket(rest: str) -> tuple[str, dict[str, str]]:
    """Split a trailing ``[k:v k:v]`` bracket off a shorthand message tail.

    Args:
        rest: The message tail left after the class head and severity bracket
            have been consumed.

    Returns:
        tuple[str, dict[str, str]]: ``(summary, bracket_fields)``. If no
        trailing bracket is present, ``bracket_fields`` is empty and
        ``summary`` is *rest* stripped.
    """
    match = _TRAILING_BRACKET_RE.search(rest)
    if match is None:
        return rest.strip(), {}
    body = match.group("body").strip()
    summary = rest[: match.start()].strip()
    fields: dict[str, str] = {}
    if body:
        for token in body.split():
            key, sep, value = token.partition(":")
            if sep:
                fields[key] = value
    return summary, fields


def _extract_binary(summary: str, bracket_fields: dict[str, str]) -> str | None:
    """Best-effort extraction of the triggering binary path.

    For connection-oriented events (NET/HTTP/SSH/PROC), the shorthand summary
    starts with ``<absolute-path>(<pid>)``; the path is the binary. For
    FINDING/EVENT/CONFIG events there is usually no such marker, but the
    upstream sandbox sometimes includes ``binary:<path>`` in the trailing
    bracket — we honour that as a secondary source.

    Args:
        summary: The message body after the class head, severity bracket,
            and disposition have been consumed.
        bracket_fields: Key/value pairs parsed from the trailing bracket.

    Returns:
        str | None: The absolute binary path if one could be identified,
        else ``None``.
    """
    match = _BINARY_RE.search(summary)
    if match is not None:
        return match.group("binary")
    if "binary" in bracket_fields and bracket_fields["binary"].startswith("/"):
        return bracket_fields["binary"]
    return None


def _extract_disposition(summary: str) -> tuple[str | None, str]:
    """Pull an ``ALLOWED``/``DENIED``/``BLOCKED`` token off the front of *summary*.

    Args:
        summary: The message body after the class head and severity bracket.

    Returns:
        tuple[str | None, str]: ``(disposition, remaining_summary)``. If
        *summary* does not start with a known disposition token the first
        element is ``None`` and *summary* is returned unchanged.
    """
    parts = summary.split(maxsplit=1)
    if not parts:
        return None, summary
    first = parts[0].upper()
    if first in _DISPOSITIONS:
        return first, parts[1] if len(parts) > 1 else ""
    return None, summary


def parse_log_line(log: dict[str, Any]) -> dict[str, Any] | None:
    """Parse an OCSF shorthand log line into a structured dict.

    The parser is permissive: unknown class prefixes, missing severities, and
    malformed bodies all fall back to ``None`` fields with the raw message
    preserved in ``summary``. It never raises on well-typed input.

    Args:
        log: A flattened sandbox log line dict as produced by the
            ``SandboxManager`` client layer (keys ``level``, ``target``,
            ``message``, ``fields``, ...).

    Returns:
        dict[str, Any] | None: ``None`` if *log* is not an OCSF entry.
        Otherwise a dict with the stable shape::

            {
                "class_prefix": "NET" | ... | "EVENT" | None,
                "activity": "OPEN" | ... | None,
                "severity": "INFO" | ... | None,
                "disposition": "ALLOWED" | "DENIED" | "BLOCKED" | None,
                "summary": str,
                "bracket_fields": dict[str, str],
                "fields": dict[str, str],
                "binary": str | None,
            }

        The ``binary`` field is a best-effort extraction of the triggering
        process path (e.g. ``/usr/bin/curl``) from either the summary's
        ``<path>(pid)`` prefix or a ``binary:`` entry in the trailing
        bracket. It is ``None`` when the shorthand did not include one.
    """
    if not _is_ocsf(log):
        return None

    message = str(log.get("message") or "").strip()
    grpc_fields_raw = log.get("fields") or {}
    grpc_fields = {str(k): str(v) for k, v in grpc_fields_raw.items()}

    result: dict[str, Any] = {
        "class_prefix": None,
        "activity": None,
        "severity": None,
        "disposition": None,
        "summary": message,
        "bracket_fields": {},
        "fields": grpc_fields,
        "binary": None,
    }

    if not message:
        return result

    head_match = _HEAD_RE.match(message)
    if head_match is None:
        return result

    prefix = head_match.group("prefix")
    suffix = head_match.group("suffix")
    rest = head_match.group("rest")

    if prefix in _CLASS_PREFIXES:
        result["class_prefix"] = prefix

    # Severity bracket (optional).
    sev_match = _SEVERITY_RE.match(rest)
    if sev_match is not None:
        sev = sev_match.group("severity")
        if sev in _SEVERITIES:
            result["severity"] = sev
        rest = sev_match.group("rest")

    # Trailing [k:v ...] bracket (optional).
    summary, bracket_fields = _split_trailing_bracket(rest)
    result["bracket_fields"] = bracket_fields

    # Interpret the suffix based on class.
    #
    # * FINDING emits ``FINDING:<DISPOSITION>`` — suffix is the disposition,
    #   activity stays None.
    # * CONFIG emits ``CONFIG:<STATE>`` — suffix is a state word; we surface
    #   it as ``activity`` for uniformity (the frontend just shows the token).
    # * EVENT has no suffix.
    # * Standard classes (NET/HTTP/SSH/PROC/LIFECYCLE) put the activity in the
    #   suffix, and — for connection classes — a disposition word at the head
    #   of the summary.
    if result["class_prefix"] == "FINDING" and suffix in _DISPOSITIONS:
        result["disposition"] = suffix
        result["summary"] = summary
    elif result["class_prefix"] == "EVENT":
        result["summary"] = summary
    else:
        if suffix:
            result["activity"] = suffix
        disposition, summary = _extract_disposition(summary)
        result["disposition"] = disposition
        result["summary"] = summary

    # Best-effort binary path — used by the sandbox-logs viewer to build a
    # cross-link into the approvals page for DENIED/BLOCKED events.
    result["binary"] = _extract_binary(result["summary"], bracket_fields)

    return result
