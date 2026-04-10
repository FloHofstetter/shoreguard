"""Tests for the OCSF shorthand log parser."""

from __future__ import annotations

from shoreguard.services.ocsf import parse_log_line


def _ocsf(message: str, fields: dict[str, str] | None = None) -> dict:
    return {
        "timestamp_ms": 1000,
        "level": "OCSF",
        "target": "ocsf",
        "source": "sandbox",
        "message": message,
        "fields": fields or {},
    }


class TestNonOcsf:
    def test_info_level_returns_none(self):
        log = {"level": "INFO", "target": "openshell_sandbox", "message": "Starting"}
        assert parse_log_line(log) is None

    def test_warn_level_returns_none(self):
        log = {"level": "WARN", "target": "something", "message": "foo"}
        assert parse_log_line(log) is None

    def test_empty_log_returns_none(self):
        assert parse_log_line({}) is None

    def test_ocsf_level_lowercase_also_detected(self):
        log = {"level": "ocsf", "target": "", "message": "NET:OPEN [INFO] ALLOWED foo"}
        result = parse_log_line(log)
        assert result is not None
        assert result["class_prefix"] == "NET"

    def test_target_ocsf_alone_is_enough(self):
        log = {"level": "", "target": "ocsf", "message": "NET:OPEN [INFO] ALLOWED foo"}
        result = parse_log_line(log)
        assert result is not None


class TestNetworkActivity:
    def test_allowed_connection(self):
        msg = (
            "NET:OPEN [INFO] ALLOWED /usr/bin/curl(58) -> api.github.com:443 "
            "[policy:github_api engine:opa]"
        )
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "NET"
        assert result["activity"] == "OPEN"
        assert result["severity"] == "INFO"
        assert result["disposition"] == "ALLOWED"
        assert result["summary"] == "/usr/bin/curl(58) -> api.github.com:443"
        assert result["bracket_fields"] == {"policy": "github_api", "engine": "opa"}

    def test_denied_connection(self):
        msg = "NET:OPEN [MED] DENIED /usr/bin/curl(64) -> httpbin.org:443 [policy:- engine:opa]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "NET"
        assert result["activity"] == "OPEN"
        assert result["severity"] == "MED"
        assert result["disposition"] == "DENIED"
        assert result["bracket_fields"] == {"policy": "-", "engine": "opa"}

    def test_grpc_fields_passed_through(self):
        result = parse_log_line(_ocsf("NET:OPEN [INFO] ALLOWED foo", fields={"dst_host": "x.y"}))
        assert result is not None
        assert result["fields"] == {"dst_host": "x.y"}


class TestHttpActivity:
    def test_http_get(self):
        msg = "HTTP:GET [INFO] ALLOWED GET http://api.github.com/zen [policy:github_api]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "HTTP"
        assert result["activity"] == "GET"
        assert result["disposition"] == "ALLOWED"
        assert result["summary"] == "GET http://api.github.com/zen"
        assert result["bracket_fields"] == {"policy": "github_api"}


class TestSshActivity:
    def test_ssh_open(self):
        msg = "SSH:OPEN [INFO] ALLOWED user@host:22 [auth:key]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "SSH"
        assert result["activity"] == "OPEN"
        assert result["disposition"] == "ALLOWED"
        assert result["bracket_fields"] == {"auth": "key"}


class TestProcessActivity:
    def test_proc_launch_no_disposition(self):
        msg = "PROC:LAUNCH [INFO] /usr/bin/curl [exit:0]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "PROC"
        assert result["activity"] == "LAUNCH"
        assert result["severity"] == "INFO"
        assert result["disposition"] is None
        assert result["summary"] == "/usr/bin/curl"
        assert result["bracket_fields"] == {"exit": "0"}

    def test_proc_terminate(self):
        msg = "PROC:TERMINATE [INFO] pid=57 [exit:0]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["activity"] == "TERMINATE"
        assert result["disposition"] is None


class TestFinding:
    def test_finding_blocked(self):
        msg = 'FINDING:BLOCKED [HIGH] "policy violation detected" [confidence:high]'
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "FINDING"
        assert result["activity"] is None
        assert result["severity"] == "HIGH"
        assert result["disposition"] == "BLOCKED"
        assert result["summary"] == '"policy violation detected"'
        assert result["bracket_fields"] == {"confidence": "high"}

    def test_finding_denied(self):
        msg = 'FINDING:DENIED [MED] "syscall blocked"'
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["disposition"] == "DENIED"
        assert result["bracket_fields"] == {}


class TestConfig:
    def test_config_loaded(self):
        msg = "CONFIG:LOADED [INFO] policy reloaded [version:v2 hash:abc]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "CONFIG"
        assert result["activity"] == "LOADED"
        assert result["severity"] == "INFO"
        assert result["disposition"] is None
        assert result["summary"] == "policy reloaded"
        assert result["bracket_fields"] == {"version": "v2", "hash": "abc"}


class TestLifecycle:
    def test_lifecycle_start(self):
        msg = "LIFECYCLE:START [INFO] sandbox ready"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "LIFECYCLE"
        assert result["activity"] == "START"
        assert result["severity"] == "INFO"
        assert result["summary"] == "sandbox ready"
        assert result["bracket_fields"] == {}


class TestEvent:
    def test_event_no_suffix(self):
        msg = "EVENT [INFO] generic event message"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "EVENT"
        assert result["activity"] is None
        assert result["disposition"] is None
        assert result["summary"] == "generic event message"


class TestBinaryExtraction:
    def test_net_open_extracts_binary_from_summary(self):
        msg = "NET:OPEN [INFO] ALLOWED /usr/bin/curl(58) -> api.github.com:443 [policy:github_api]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["binary"] == "/usr/bin/curl"

    def test_http_get_extracts_binary(self):
        msg = "HTTP:GET [INFO] ALLOWED /usr/bin/python3(1234) GET http://x [policy:p]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["binary"] == "/usr/bin/python3"

    def test_proc_launch_extracts_binary(self):
        msg = "PROC:LAUNCH [INFO] /usr/bin/curl(57) [exit:0]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["binary"] == "/usr/bin/curl"

    def test_bracket_binary_fallback(self):
        # No "<path>(pid)" marker in the summary, but bracket has binary:<path>.
        msg = 'FINDING:BLOCKED [HIGH] "policy violation" [binary:/opt/app/main confidence:high]'
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["binary"] == "/opt/app/main"

    def test_no_binary_when_absent(self):
        msg = "LIFECYCLE:START [INFO] sandbox ready"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["binary"] is None

    def test_bracket_binary_ignored_when_not_absolute(self):
        # Relative paths or bare names from the bracket are rejected — we
        # only trust an absolute path as a binary.
        msg = "EVENT [INFO] something happened [binary:curl]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["binary"] is None


class TestMalformed:
    def test_unknown_class_prefix(self):
        msg = "QUIC:FOO [INFO] bar baz"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] is None
        # Head regex still consumed the prefix; parser continues with rest.
        assert "bar baz" in result["summary"]

    def test_missing_severity(self):
        msg = "NET:OPEN ALLOWED /usr/bin/curl -> example.com:80"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["class_prefix"] == "NET"
        assert result["severity"] is None
        assert result["disposition"] == "ALLOWED"

    def test_empty_message(self):
        result = parse_log_line(_ocsf(""))
        assert result is not None
        assert result["class_prefix"] is None
        assert result["summary"] == ""

    def test_no_trailing_bracket(self):
        msg = "NET:OPEN [INFO] ALLOWED foo bar"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        assert result["bracket_fields"] == {}
        assert result["summary"] == "foo bar"

    def test_bracket_without_kv_pairs(self):
        msg = "NET:OPEN [INFO] ALLOWED foo [nokey]"
        result = parse_log_line(_ocsf(msg))
        assert result is not None
        # "nokey" without ':' is dropped silently
        assert result["bracket_fields"] == {}
