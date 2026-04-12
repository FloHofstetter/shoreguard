"""Tests for bypass detection — OCSF classify_bypass + BypassService."""

from __future__ import annotations

from shoreguard.services.bypass import BypassService
from shoreguard.services.ocsf import classify_bypass, parse_log_line

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _ocsf(message: str, fields: dict[str, str] | None = None) -> dict:
    return {
        "timestamp_ms": 1000,
        "level": "OCSF",
        "target": "ocsf",
        "source": "sandbox",
        "message": message,
        "fields": fields or {},
    }


def _parsed(message: str, **extra: str) -> dict:
    """Parse an OCSF shorthand message for use with classify_bypass."""
    return parse_log_line(_ocsf(message, fields=extra))  # type: ignore[return-value]


# ─── classify_bypass unit tests ──────────────────────────────────────────────


class TestClassifyBypassNone:
    """Events that should NOT be classified as bypass."""

    def test_none_input(self):
        assert classify_bypass(None) is None  # type: ignore[arg-type]

    def test_normal_net_allowed(self):
        msg = "NET:OPEN [INFO] ALLOWED /usr/bin/curl(58) -> api.github.com:443 [engine:opa]"
        assert classify_bypass(_parsed(msg)) is None

    def test_normal_net_denied_opa(self):
        parsed = _parsed("NET:OPEN [MED] DENIED /usr/bin/curl(64) -> httpbin.org:443 [engine:opa]")
        assert classify_bypass(parsed) is None

    def test_finding_low_severity(self):
        parsed = _parsed('FINDING:BLOCKED [LOW] "something minor" [confidence:low]')
        assert classify_bypass(parsed) is None

    def test_finding_info_severity(self):
        parsed = _parsed('FINDING:DENIED [INFO] "informational notice"')
        assert classify_bypass(parsed) is None

    def test_config_event(self):
        parsed = _parsed("CONFIG:LOADED [INFO] policy reloaded [version:v2]")
        assert classify_bypass(parsed) is None

    def test_lifecycle_event(self):
        parsed = _parsed("LIFECYCLE:START [INFO] sandbox ready")
        assert classify_bypass(parsed) is None

    def test_proc_launch(self):
        parsed = _parsed("PROC:LAUNCH [INFO] /usr/bin/curl(57) [exit:0]")
        assert classify_bypass(parsed) is None

    def test_finding_allowed_not_bypass(self):
        parsed = _parsed('FINDING:ALLOWED [HIGH] "not a denial"')
        assert classify_bypass(parsed) is None


class TestClassifyBypassFinding:
    """FINDING events with high severity + deny/block disposition."""

    def test_finding_blocked_high(self):
        parsed = _parsed('FINDING:BLOCKED [HIGH] "bypass attempt detected" [confidence:high]')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["severity"] == "HIGH"
        assert result["technique"] == "bypass"
        assert result["mitre_id"] == "T1562.004"
        assert result["raw_class"] == "FINDING"
        assert result["raw_disposition"] == "BLOCKED"

    def test_finding_denied_crit(self):
        parsed = _parsed('FINDING:DENIED [CRIT] "critical policy bypass"')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["severity"] == "CRIT"
        assert result["raw_disposition"] == "DENIED"

    def test_finding_blocked_fatal(self):
        parsed = _parsed('FINDING:BLOCKED [FATAL] "fatal bypass"')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["severity"] == "FATAL"

    def test_finding_with_binary(self):
        parsed = _parsed('FINDING:BLOCKED [HIGH] "bypass" [binary:/usr/bin/nc confidence:high]')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["binary"] == "/usr/bin/nc"


class TestClassifyBypassIptables:
    """NET denial with engine:iptables (kernel-level bypass detection)."""

    def test_net_denied_iptables(self):
        parsed = _parsed(
            "NET:OPEN [HIGH] DENIED /usr/bin/python3(100) -> evil.com:443 [engine:iptables]"
        )
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "iptables"
        assert result["mitre_id"] == "T1562.004"
        assert result["binary"] == "/usr/bin/python3"
        assert result["raw_class"] == "NET"

    def test_net_blocked_iptables(self):
        parsed = _parsed("NET:OPEN [MED] BLOCKED dst=10.0.0.1:8080 [engine:iptables]")
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "iptables"

    def test_net_denied_opa_not_bypass(self):
        """OPA denials are regular policy denials, NOT bypass attempts."""
        parsed = _parsed("NET:OPEN [MED] DENIED /usr/bin/curl(64) -> x.com:443 [engine:opa]")
        assert classify_bypass(parsed) is None


class TestClassifyBypassKeywords:
    """Events matching bypass indicator keywords in summary or brackets."""

    def test_bypass_keyword_in_summary(self):
        parsed = _parsed("NET:OPEN [INFO] DENIED bypass attempt via raw socket [engine:opa]")
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "bypass"

    def test_nsenter_keyword(self):
        parsed = _parsed('FINDING:BLOCKED [HIGH] "nsenter escape to host namespace"')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "nsenter"
        assert result["mitre_id"] == "T1611"

    def test_unshare_keyword(self):
        parsed = _parsed('FINDING:BLOCKED [HIGH] "unshare -n namespace breakout"')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "unshare"
        assert result["mitre_id"] == "T1611"

    def test_nftables_keyword_in_brackets(self):
        parsed = _parsed('FINDING:DENIED [HIGH] "firewall rule modified" [tool:nftables]')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "nftables"
        assert result["mitre_id"] == "T1562.004"

    def test_iptables_keyword_in_summary(self):
        parsed = _parsed('FINDING:BLOCKED [HIGH] "iptables rule flush detected"')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "iptables"

    def test_netns_keyword(self):
        parsed = _parsed('FINDING:BLOCKED [HIGH] "netns escape attempt"')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "netns"
        assert result["mitre_id"] == "T1611"

    def test_keyword_match_is_case_insensitive(self):
        # Summary is lowercased before keyword matching.
        parsed = _parsed('FINDING:BLOCKED [HIGH] "BYPASS attempt"')
        result = classify_bypass(parsed)
        assert result is not None
        assert result["technique"] == "bypass"


# ─── BypassService unit tests ────────────────────────────────────────────────


class TestBypassServiceIngest:
    """Ingest logs and detect bypass events."""

    def test_ingest_bypass_event(self):
        svc = BypassService(ring_size=100)
        log = _ocsf('FINDING:BLOCKED [HIGH] "bypass attempt" [confidence:high]')
        record = svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")
        assert record is not None
        assert record["sandbox_name"] == "sb1"
        assert record["gateway_name"] == "gw1"
        assert record["event"]["technique"] == "bypass"

    def test_ingest_normal_event_returns_none(self):
        svc = BypassService(ring_size=100)
        log = _ocsf("NET:OPEN [INFO] ALLOWED /usr/bin/curl(58) -> api.github.com:443 [engine:opa]")
        assert svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1") is None

    def test_ingest_non_ocsf_returns_none(self):
        svc = BypassService(ring_size=100)
        log = {"level": "INFO", "target": "openshell", "message": "Starting"}
        assert svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1") is None

    def test_ingest_preserves_timestamp(self):
        svc = BypassService(ring_size=100)
        log = _ocsf('FINDING:BLOCKED [HIGH] "bypass"')
        log["timestamp_ms"] = 42000
        record = svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")
        assert record is not None
        assert record["timestamp_ms"] == 42000

    def test_ingest_generates_timestamp_when_missing(self):
        svc = BypassService(ring_size=100)
        log = _ocsf('FINDING:BLOCKED [HIGH] "bypass"')
        del log["timestamp_ms"]
        record = svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")
        assert record is not None
        assert record["timestamp_ms"] > 0

    def test_ring_buffer_evicts_oldest(self):
        svc = BypassService(ring_size=3)
        for i in range(5):
            log = _ocsf(f'FINDING:BLOCKED [HIGH] "bypass #{i}"')
            log["timestamp_ms"] = 1000 + i
            svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")
        events = svc.get_events("gw1", "sb1", limit=10)
        assert len(events) == 3
        # Newest first.
        assert events[0]["timestamp_ms"] == 1004
        assert events[2]["timestamp_ms"] == 1002


class TestBypassServiceGetEvents:
    """Query events with since_ms and limit."""

    def _populate(self, svc: BypassService, count: int = 10) -> None:
        for i in range(count):
            log = _ocsf(f'FINDING:BLOCKED [HIGH] "bypass #{i}"')
            log["timestamp_ms"] = 1000 + i
            svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")

    def test_empty_sandbox(self):
        svc = BypassService()
        assert svc.get_events("gw1", "sb1") == []

    def test_limit(self):
        svc = BypassService()
        self._populate(svc, 10)
        events = svc.get_events("gw1", "sb1", limit=3)
        assert len(events) == 3

    def test_since_ms_filter(self):
        svc = BypassService()
        self._populate(svc, 10)
        events = svc.get_events("gw1", "sb1", since_ms=1007, limit=100)
        assert len(events) == 3
        assert all(e["timestamp_ms"] >= 1007 for e in events)

    def test_newest_first_ordering(self):
        svc = BypassService()
        self._populate(svc, 5)
        events = svc.get_events("gw1", "sb1", limit=100)
        timestamps = [e["timestamp_ms"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_isolation_between_sandboxes(self):
        svc = BypassService()
        log1 = _ocsf('FINDING:BLOCKED [HIGH] "bypass sb1"')
        log1["timestamp_ms"] = 1000
        svc.ingest_log(log1, sandbox_name="sb1", gateway_name="gw1")

        log2 = _ocsf('FINDING:BLOCKED [HIGH] "bypass sb2"')
        log2["timestamp_ms"] = 2000
        svc.ingest_log(log2, sandbox_name="sb2", gateway_name="gw1")

        assert len(svc.get_events("gw1", "sb1")) == 1
        assert len(svc.get_events("gw1", "sb2")) == 1

    def test_isolation_between_gateways(self):
        svc = BypassService()
        log = _ocsf('FINDING:BLOCKED [HIGH] "bypass"')
        log["timestamp_ms"] = 1000
        svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")
        svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw2")

        assert len(svc.get_events("gw1", "sb1")) == 1
        assert len(svc.get_events("gw2", "sb1")) == 1


class TestBypassServiceSummary:
    """get_summary aggregation."""

    def test_empty_sandbox(self):
        svc = BypassService()
        summary = svc.get_summary("gw1", "sb1")
        assert summary["total"] == 0
        assert summary["by_technique"] == {}
        assert summary["by_severity"] == {}
        assert summary["latest_timestamp_ms"] is None

    def test_aggregation(self):
        svc = BypassService()
        # Two iptables events.
        for i in range(2):
            log = _ocsf(f"NET:OPEN [HIGH] DENIED dst:evil.com:{i} [engine:iptables]")
            log["timestamp_ms"] = 1000 + i
            svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")

        # One nsenter event.
        log = _ocsf('FINDING:BLOCKED [CRIT] "nsenter escape"')
        log["timestamp_ms"] = 2000
        svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")

        summary = svc.get_summary("gw1", "sb1")
        assert summary["total"] == 3
        assert summary["by_technique"]["iptables"] == 2
        assert summary["by_technique"]["nsenter"] == 1
        assert summary["by_severity"]["HIGH"] == 2
        assert summary["by_severity"]["CRIT"] == 1
        assert summary["latest_timestamp_ms"] == 2000


class TestBypassServiceClear:
    """clear() removes all events for a sandbox."""

    def test_clear(self):
        svc = BypassService()
        log = _ocsf('FINDING:BLOCKED [HIGH] "bypass"')
        svc.ingest_log(log, sandbox_name="sb1", gateway_name="gw1")
        assert svc.get_summary("gw1", "sb1")["total"] == 1

        svc.clear("gw1", "sb1")
        assert svc.get_summary("gw1", "sb1")["total"] == 0
        assert svc.get_events("gw1", "sb1") == []

    def test_clear_nonexistent_is_noop(self):
        svc = BypassService()
        svc.clear("gw1", "sb1")  # Should not raise.
