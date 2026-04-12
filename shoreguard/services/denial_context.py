"""Denial context cache — in-memory store for DenialSummary enrichment.

The OpenShell gateway does not expose a ``GetDenialSummary`` RPC.
``DenialSummary`` objects only flow *inbound* (via ``SubmitPolicyAnalysis``),
while ``GetDraftPolicy`` returns ``PolicyChunk`` objects that reference
summaries by opaque ``denial_summary_ids``.

This service captures denial summaries as they pass through
:meth:`~shoreguard.services.policy.PolicyService.submit_analysis` and caches
them keyed by ``(sandbox, binary, host, port)``.  When the approvals service
reads draft chunks, it enriches each chunk with the cached denial context
(process ancestry, binary SHA256, L7 request samples, persistent flag).

No database persistence — the cache is tied to the application lifecycle.
If summaries were submitted before this process started, chunks degrade
gracefully to the existing display (no enrichment, ``denial_context: None``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

#: Default maximum cached summaries per sandbox.
DEFAULT_MAX_ENTRIES = 500


class L7Sample(TypedDict):
    """A single L7 (HTTP) request sample from a denial.

    Attributes:
        method: HTTP method (GET, POST, …).
        path: URL path of the request.
        decision: Gateway decision (allow / deny).
        count: Number of times this pattern was observed.
    """

    method: str
    path: str
    decision: str
    count: int


class DenialSummaryRecord(TypedDict):
    """Cached denial summary enrichment fields.

    Attributes:
        ancestors: Process ancestry chain (e.g. ``["bash", "python", "curl"]``).
        binary_sha256: SHA-256 hex digest of the denied binary.
        persistent: Whether the denial is recurring.
        l7_request_samples: HTTP-level request samples.
        l7_inspection_active: Whether L7 inspection was active.
        deny_reason: Why the request was denied.
        sample_cmdlines: Example command lines from the denied process.
        denial_stage: Stage at which the denial occurred.
        count: Denial count.
        total_count: Total denial count including suppressed.
        cached_at_ms: Timestamp when this record was cached.
    """

    ancestors: list[str]
    binary_sha256: str
    persistent: bool
    l7_request_samples: list[L7Sample]
    l7_inspection_active: bool
    deny_reason: str
    sample_cmdlines: list[str]
    denial_stage: str
    count: int
    total_count: int
    cached_at_ms: int


def _normalize_key(sandbox: str, binary: str, host: str, port: int) -> tuple[str, str, str, int]:
    """Normalize a cache key for consistent lookups.

    Args:
        sandbox: Sandbox name.
        binary: Binary path.
        host: Target host.
        port: Target port.

    Returns:
        tuple[str, str, str, int]: Normalized 4-tuple.
    """
    return (
        sandbox.strip(),
        binary.strip(),
        host.strip().lower().rstrip("."),
        int(port),
    )


class DenialContextService:
    """In-memory denial summary cache for chunk enrichment.

    Thread-safe: the policy submission and approval read paths may run
    concurrently.

    Args:
        max_entries: Maximum cached summaries per sandbox (oldest evicted).
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:  # noqa: D107
        self._max_entries = max_entries
        self._lock = threading.Lock()
        # sandbox_name -> OrderedDict[(binary, host, port) -> DenialSummaryRecord]
        self._caches: dict[str, OrderedDict[tuple[str, str, int], DenialSummaryRecord]] = {}

    def ingest_summaries(self, sandbox_name: str, summaries: list[dict[str, Any]]) -> int:
        """Parse and cache denial summaries from a policy analysis submission.

        Args:
            sandbox_name: Target sandbox.
            summaries: Raw ``DenialSummary`` dicts as received from the
                analyzer.

        Returns:
            int: Number of summaries successfully cached.
        """
        if not summaries:
            return 0

        now_ms = int(time.time() * 1000)
        stored = 0

        with self._lock:
            cache = self._caches.get(sandbox_name)
            if cache is None:
                cache = OrderedDict()
                self._caches[sandbox_name] = cache

            for s in summaries:
                binary = str(s.get("binary") or "").strip()
                host = str(s.get("host") or "").strip().lower().rstrip(".")
                port = int(s.get("port") or 0)

                if not host:
                    continue

                key = (binary, host, port)

                # Parse L7 samples.
                raw_l7 = s.get("l7_request_samples") or []
                l7_samples: list[L7Sample] = [
                    L7Sample(
                        method=str(sample.get("method") or ""),
                        path=str(sample.get("path") or ""),
                        decision=str(sample.get("decision") or ""),
                        count=int(sample.get("count") or 0),
                    )
                    for sample in raw_l7
                    if isinstance(sample, dict)
                ]

                record = DenialSummaryRecord(
                    ancestors=list(s.get("ancestors") or []),
                    binary_sha256=str(s.get("binary_sha256") or ""),
                    persistent=bool(s.get("persistent")),
                    l7_request_samples=l7_samples,
                    l7_inspection_active=bool(s.get("l7_inspection_active")),
                    deny_reason=str(s.get("deny_reason") or ""),
                    sample_cmdlines=list(s.get("sample_cmdlines") or []),
                    denial_stage=str(s.get("denial_stage") or ""),
                    count=int(s.get("count") or 0),
                    total_count=int(s.get("total_count") or 0),
                    cached_at_ms=now_ms,
                )

                # Move to end if updating, then evict oldest if over limit.
                if key in cache:
                    cache.move_to_end(key)
                cache[key] = record
                stored += 1

                while len(cache) > self._max_entries:
                    cache.popitem(last=False)

        if stored:
            logger.debug(
                "Cached %d denial summaries for sandbox '%s'",
                stored,
                sandbox_name,
            )
        return stored

    def lookup(
        self,
        sandbox_name: str,
        binary: str,
        host: str,
        port: int,
    ) -> DenialSummaryRecord | None:
        """Look up a cached denial summary by exact key match.

        Args:
            sandbox_name: Sandbox name.
            binary: Binary path.
            host: Target host.
            port: Target port.

        Returns:
            DenialSummaryRecord | None: The cached record, or ``None``
                if no match.
        """
        _, norm_binary, norm_host, norm_port = _normalize_key(sandbox_name, binary, host, port)
        key = (norm_binary, norm_host, norm_port)
        with self._lock:
            cache = self._caches.get(sandbox_name.strip())
            if cache is None:
                return None
            return cache.get(key)

    def enrich_chunks(
        self,
        sandbox_name: str,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Enrich approval chunks with cached denial context.

        For each chunk, extracts ``binary`` and the first endpoint's
        ``(host, port)`` from ``proposed_rule``, looks up a matching cached
        summary, and sets ``chunk["denial_context"]`` to the record (or
        ``None``).

        Args:
            sandbox_name: Sandbox name.
            chunks: List of chunk dicts as returned by
                :meth:`ApprovalManager.get_draft`.

        Returns:
            list[dict[str, Any]]: The same list, with ``denial_context``
                added to each chunk.
        """
        for chunk in chunks:
            chunk["denial_context"] = self._match_chunk(sandbox_name, chunk)
        return chunks

    def _match_chunk(self, sandbox_name: str, chunk: dict[str, Any]) -> DenialSummaryRecord | None:
        """Find the best-matching cached summary for a chunk.

        Tries each endpoint in order and returns the first match.

        Args:
            sandbox_name: Sandbox name.
            chunk: Chunk dict with ``binary`` and ``proposed_rule``.

        Returns:
            DenialSummaryRecord | None: Matched record or ``None``.
        """
        binary = str(chunk.get("binary") or "")
        rule = chunk.get("proposed_rule")
        if not isinstance(rule, dict):
            return None
        endpoints = rule.get("endpoints")
        if not endpoints:
            return None

        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            host = str(ep.get("host") or "")
            port = int(ep.get("port") or 0)
            if not host:
                continue
            record = self.lookup(sandbox_name, binary, host, port)
            if record is not None:
                return record
        return None

    def clear(self, sandbox_name: str) -> None:
        """Remove all cached summaries for a sandbox.

        Args:
            sandbox_name: Sandbox name.
        """
        with self._lock:
            self._caches.pop(sandbox_name.strip(), None)


#: Module-level singleton, initialised during app lifespan.
denial_context_service: DenialContextService | None = None
