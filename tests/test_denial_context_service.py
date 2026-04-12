"""Tests for DenialContextService — in-memory denial summary cache."""

from __future__ import annotations

from shoreguard.services.denial_context import DenialContextService

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _summary(
    binary: str = "/usr/bin/curl",
    host: str = "api.example.com",
    port: int = 443,
    *,
    ancestors: list[str] | None = None,
    binary_sha256: str = "abcd1234" * 8,
    persistent: bool = False,
    l7_request_samples: list[dict] | None = None,
    deny_reason: str = "policy_denied",
    count: int = 5,
) -> dict:
    """Build a DenialSummary dict for testing."""
    return {
        "sandbox_id": "sb1",
        "host": host,
        "port": port,
        "binary": binary,
        "ancestors": ancestors or ["bash", "python", "curl"],
        "deny_reason": deny_reason,
        "first_seen_ms": 1000,
        "last_seen_ms": 2000,
        "count": count,
        "suppressed_count": 0,
        "total_count": count,
        "sample_cmdlines": ["curl https://api.example.com"],
        "binary_sha256": binary_sha256,
        "persistent": persistent,
        "denial_stage": "network",
        "l7_request_samples": l7_request_samples or [],
        "l7_inspection_active": bool(l7_request_samples),
    }


def _chunk(
    chunk_id: str = "chunk-1",
    binary: str = "/usr/bin/curl",
    host: str = "api.example.com",
    port: int = 443,
) -> dict:
    """Build a minimal PolicyChunk dict for enrichment tests."""
    return {
        "id": chunk_id,
        "status": "pending",
        "rule_name": "allow-api",
        "binary": binary,
        "proposed_rule": {
            "name": "allow-api",
            "endpoints": [{"host": host, "port": port}],
            "binaries": [{"path": binary}],
        },
        "denial_summary_ids": ["ds-1"],
        "hit_count": 5,
    }


# ─── Ingest ───────────────────────────────────────────────────────────────────


class TestIngest:
    """ingest_summaries stores summaries and returns counts."""

    def test_ingest_single(self):
        svc = DenialContextService()
        count = svc.ingest_summaries("sb1", [_summary()])
        assert count == 1

    def test_ingest_multiple(self):
        svc = DenialContextService()
        summaries = [
            _summary(host="a.com", port=80),
            _summary(host="b.com", port=443),
        ]
        assert svc.ingest_summaries("sb1", summaries) == 2

    def test_ingest_empty_list(self):
        svc = DenialContextService()
        assert svc.ingest_summaries("sb1", []) == 0

    def test_ingest_overwrite_same_key(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(persistent=False)])
        svc.ingest_summaries("sb1", [_summary(persistent=True)])
        record = svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443)
        assert record is not None
        assert record["persistent"] is True

    def test_ingest_skips_missing_host(self):
        svc = DenialContextService()
        s = _summary()
        s["host"] = ""
        assert svc.ingest_summaries("sb1", [s]) == 0

    def test_ingest_l7_samples(self):
        svc = DenialContextService()
        samples = [
            {"method": "GET", "path": "/api/v1/data", "decision": "deny", "count": 3},
            {"method": "POST", "path": "/api/v1/upload", "decision": "deny", "count": 1},
        ]
        svc.ingest_summaries("sb1", [_summary(l7_request_samples=samples)])
        record = svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443)
        assert record is not None
        assert len(record["l7_request_samples"]) == 2
        assert record["l7_request_samples"][0]["method"] == "GET"
        assert record["l7_request_samples"][0]["path"] == "/api/v1/data"
        assert record["l7_request_samples"][1]["count"] == 1

    def test_ingest_ancestors(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(ancestors=["init", "bash", "python3", "curl"])])
        record = svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443)
        assert record is not None
        assert record["ancestors"] == ["init", "bash", "python3", "curl"]


# ─── Lookup ───────────────────────────────────────────────────────────────────


class TestLookup:
    """Exact-match lookup by (sandbox, binary, host, port)."""

    def test_miss_returns_none(self):
        svc = DenialContextService()
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443) is None

    def test_exact_match(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(binary_sha256="deadbeef" * 8)])
        record = svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443)
        assert record is not None
        assert record["binary_sha256"] == "deadbeef" * 8

    def test_sandbox_isolation(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary()])
        assert svc.lookup("sb2", "/usr/bin/curl", "api.example.com", 443) is None

    def test_different_port_no_match(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(port=443)])
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 80) is None

    def test_different_binary_no_match(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(binary="/usr/bin/curl")])
        assert svc.lookup("sb1", "/usr/bin/wget", "api.example.com", 443) is None


# ─── Normalization ────────────────────────────────────────────────────────────


class TestNormalization:
    """Key normalization: lowercase host, strip trailing dot, strip whitespace."""

    def test_host_case_insensitive(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(host="API.Example.COM")])
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443) is not None

    def test_host_trailing_dot(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(host="api.example.com.")])
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443) is not None

    def test_lookup_normalizes_too(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(host="api.example.com")])
        assert svc.lookup("sb1", "/usr/bin/curl", "API.EXAMPLE.COM.", 443) is not None

    def test_binary_whitespace_stripped(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(binary=" /usr/bin/curl ")])
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443) is not None


# ─── Enrich Chunks ────────────────────────────────────────────────────────────


class TestEnrichChunks:
    """enrich_chunks adds denial_context to chunk dicts."""

    def test_enriches_matching_chunk(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(persistent=True)])
        chunks = [_chunk()]
        svc.enrich_chunks("sb1", chunks)
        ctx = chunks[0]["denial_context"]
        assert ctx is not None
        assert ctx["persistent"] is True
        assert ctx["ancestors"] == ["bash", "python", "curl"]
        assert ctx["binary_sha256"] == "abcd1234" * 8

    def test_no_match_yields_none(self):
        svc = DenialContextService()
        chunks = [_chunk()]
        svc.enrich_chunks("sb1", chunks)
        assert chunks[0]["denial_context"] is None

    def test_no_proposed_rule_yields_none(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary()])
        chunk = {"id": "c1", "binary": "/usr/bin/curl"}
        svc.enrich_chunks("sb1", [chunk])
        assert chunk["denial_context"] is None

    def test_multi_endpoint_first_match(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary(host="b.com", port=8080, deny_reason="second")])
        chunk = _chunk()
        chunk["proposed_rule"]["endpoints"] = [
            {"host": "a.com", "port": 443},
            {"host": "b.com", "port": 8080},
        ]
        svc.enrich_chunks("sb1", [chunk])
        ctx = chunk["denial_context"]
        assert ctx is not None
        assert ctx["deny_reason"] == "second"

    def test_enriches_multiple_chunks(self):
        svc = DenialContextService()
        svc.ingest_summaries(
            "sb1",
            [
                _summary(binary="/usr/bin/curl", host="a.com"),
                _summary(binary="/usr/bin/wget", host="b.com"),
            ],
        )
        chunks = [
            _chunk("c1", binary="/usr/bin/curl", host="a.com"),
            _chunk("c2", binary="/usr/bin/wget", host="b.com"),
            _chunk("c3", binary="/usr/bin/nc", host="c.com"),
        ]
        svc.enrich_chunks("sb1", chunks)
        assert chunks[0]["denial_context"] is not None
        assert chunks[1]["denial_context"] is not None
        assert chunks[2]["denial_context"] is None

    def test_returns_same_list(self):
        svc = DenialContextService()
        chunks = [_chunk()]
        result = svc.enrich_chunks("sb1", chunks)
        assert result is chunks


# ─��─ Eviction ─────────────────────────────────────────────────────────────────


class TestEviction:
    """OrderedDict eviction when max_entries is exceeded."""

    def test_evicts_oldest(self):
        svc = DenialContextService(max_entries=3)
        for i in range(5):
            svc.ingest_summaries("sb1", [_summary(host=f"host-{i}.com")])

        # Oldest two (host-0, host-1) should be evicted.
        assert svc.lookup("sb1", "/usr/bin/curl", "host-0.com", 443) is None
        assert svc.lookup("sb1", "/usr/bin/curl", "host-1.com", 443) is None
        # Newest three should remain.
        assert svc.lookup("sb1", "/usr/bin/curl", "host-2.com", 443) is not None
        assert svc.lookup("sb1", "/usr/bin/curl", "host-3.com", 443) is not None
        assert svc.lookup("sb1", "/usr/bin/curl", "host-4.com", 443) is not None

    def test_update_refreshes_position(self):
        svc = DenialContextService(max_entries=3)
        svc.ingest_summaries("sb1", [_summary(host="a.com")])
        svc.ingest_summaries("sb1", [_summary(host="b.com")])
        svc.ingest_summaries("sb1", [_summary(host="c.com")])
        # Re-ingest a.com → moves to end.
        svc.ingest_summaries("sb1", [_summary(host="a.com", persistent=True)])
        # Ingest d.com → evicts b.com (now oldest).
        svc.ingest_summaries("sb1", [_summary(host="d.com")])

        assert svc.lookup("sb1", "/usr/bin/curl", "b.com", 443) is None
        record = svc.lookup("sb1", "/usr/bin/curl", "a.com", 443)
        assert record is not None
        assert record["persistent"] is True


# ─── Clear ────────────────────────────────────────────────────────────────────


class TestClear:
    """clear() removes all cached summaries for a sandbox."""

    def test_clear(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary()])
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443) is not None
        svc.clear("sb1")
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443) is None

    def test_clear_nonexistent_is_noop(self):
        svc = DenialContextService()
        svc.clear("sb1")  # Should not raise.

    def test_clear_does_not_affect_other_sandboxes(self):
        svc = DenialContextService()
        svc.ingest_summaries("sb1", [_summary()])
        svc.ingest_summaries("sb2", [_summary()])
        svc.clear("sb1")
        assert svc.lookup("sb1", "/usr/bin/curl", "api.example.com", 443) is None
        assert svc.lookup("sb2", "/usr/bin/curl", "api.example.com", 443) is not None
