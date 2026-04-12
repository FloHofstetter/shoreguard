"""Bypass detection service — in-memory ring buffer for sandbox bypass events.

OpenShell's sandbox supervisor detects network bypass attempts (traffic that
circumvents the HTTP CONNECT proxy) via iptables LOG rules and emits them as
OCSF ``FINDING`` events.  The :class:`BypassService` collects these events
from the live log stream, classifies them via
:func:`~shoreguard.services.ocsf.classify_bypass`, and stores them in a
per-sandbox ring buffer for the bypass detection dashboard.

No database persistence — bypass events are ephemeral, tied to the
application lifecycle.  The authoritative log lives in OCSF JSONL on the
gateway; this service is a **real-time view** for the UI.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, TypedDict

from shoreguard.services.ocsf import BypassEvent, classify_bypass, parse_log_line

logger = logging.getLogger(__name__)

#: Default maximum events kept per sandbox.
DEFAULT_RING_SIZE = 1000


class BypassEventRecord(TypedDict):
    """A bypass event enriched with sandbox context and timestamp.

    Attributes:
        timestamp_ms: Event timestamp in milliseconds since epoch.
        sandbox_name: Name of the sandbox that emitted the event.
        gateway_name: Name of the gateway the sandbox belongs to.
        event: The classified bypass event payload.
    """

    timestamp_ms: int
    sandbox_name: str
    gateway_name: str
    event: BypassEvent


class BypassSummary(TypedDict):
    """Aggregated bypass statistics for one sandbox.

    Attributes:
        total: Total number of bypass events.
        by_technique: Event count keyed by technique name.
        by_severity: Event count keyed by severity level.
        latest_timestamp_ms: Timestamp of the most recent event, or ``None``.
    """

    total: int
    by_technique: dict[str, int]
    by_severity: dict[str, int]
    latest_timestamp_ms: int | None


class BypassService:
    """In-memory bypass event collector with per-sandbox ring buffers.

    Thread-safe: multiple websocket streams can push events concurrently.

    Args:
        ring_size: Maximum events retained per sandbox (oldest evicted first).
    """

    def __init__(self, ring_size: int = DEFAULT_RING_SIZE) -> None:  # noqa: D107
        self._ring_size = ring_size
        self._lock = threading.Lock()
        # Key: (gateway_name, sandbox_name) -> deque of BypassEventRecord
        self._buffers: dict[tuple[str, str], deque[BypassEventRecord]] = {}

    def ingest_log(
        self,
        log: dict[str, Any],
        *,
        sandbox_name: str,
        gateway_name: str,
    ) -> BypassEventRecord | None:
        """Parse a raw log line, classify it, and store if it is a bypass event.

        Args:
            log: Raw sandbox log dict (shape from ``SandboxManager.get_logs``).
            sandbox_name: Name of the sandbox that emitted the log.
            gateway_name: Name of the gateway the sandbox belongs to.

        Returns:
            BypassEventRecord | None: The stored record if a bypass was
            detected, else ``None``.
        """
        parsed = parse_log_line(log)
        if parsed is None:
            return None

        bypass = classify_bypass(parsed)
        if bypass is None:
            return None

        ts = log.get("timestamp_ms") or int(time.time() * 1000)

        record = BypassEventRecord(
            timestamp_ms=ts,
            sandbox_name=sandbox_name,
            gateway_name=gateway_name,
            event=bypass,
        )

        key = (gateway_name, sandbox_name)
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                buf = deque(maxlen=self._ring_size)
                self._buffers[key] = buf
            buf.append(record)

        return record

    def get_events(
        self,
        gateway_name: str,
        sandbox_name: str,
        *,
        since_ms: int = 0,
        limit: int = 100,
    ) -> list[BypassEventRecord]:
        """Return bypass events for a sandbox, newest first.

        Args:
            gateway_name: Gateway name.
            sandbox_name: Sandbox name.
            since_ms: Only return events with ``timestamp_ms >= since_ms``.
            limit: Maximum number of events to return.

        Returns:
            list[BypassEventRecord]: Events in reverse chronological order.
        """
        key = (gateway_name, sandbox_name)
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                return []
            # Iterate in reverse (newest first), filter by since_ms.
            result: list[BypassEventRecord] = []
            for rec in reversed(buf):
                if rec["timestamp_ms"] >= since_ms:
                    result.append(rec)
                    if len(result) >= limit:
                        break
            return result

    def get_summary(
        self,
        gateway_name: str,
        sandbox_name: str,
    ) -> BypassSummary:
        """Return aggregated bypass statistics for a sandbox.

        Args:
            gateway_name: Gateway name.
            sandbox_name: Sandbox name.

        Returns:
            BypassSummary: Counts by technique and severity.
        """
        key = (gateway_name, sandbox_name)
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                return BypassSummary(
                    total=0,
                    by_technique={},
                    by_severity={},
                    latest_timestamp_ms=None,
                )

            by_technique: dict[str, int] = {}
            by_severity: dict[str, int] = {}
            latest: int | None = None

            for rec in buf:
                evt = rec["event"]
                tech = evt["technique"]
                sev = evt["severity"]
                by_technique[tech] = by_technique.get(tech, 0) + 1
                by_severity[sev] = by_severity.get(sev, 0) + 1
                ts = rec["timestamp_ms"]
                if latest is None or ts > latest:
                    latest = ts

            return BypassSummary(
                total=len(buf),
                by_technique=by_technique,
                by_severity=by_severity,
                latest_timestamp_ms=latest,
            )

    def clear(self, gateway_name: str, sandbox_name: str) -> None:
        """Clear all bypass events for a sandbox.

        Args:
            gateway_name: Gateway name.
            sandbox_name: Sandbox name.
        """
        key = (gateway_name, sandbox_name)
        with self._lock:
            self._buffers.pop(key, None)


#: Module-level singleton, initialised during app lifespan.
bypass_service: BypassService | None = None
