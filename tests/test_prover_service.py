"""Unit tests for the Z3 policy prover service."""

# pyright: reportOptionalSubscript=false

from __future__ import annotations

import pytest

from shoreguard.services.prover import ProverService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> ProverService:
    return ProverService(timeout_ms=5000)


def _simple_policy(
    *,
    network_policies: dict | None = None,
    filesystem: dict | None = None,
) -> dict:
    """Build a minimal policy dict for testing."""
    p: dict = {}
    if network_policies is not None:
        p["network_policies"] = network_policies
    if filesystem is not None:
        p["filesystem"] = filesystem
    return p


def _net_rule(
    host: str,
    port: int = 443,
    protocol: str = "rest",
    binaries: list[dict] | None = None,
    l7_rules: list[dict] | None = None,
) -> dict:
    """Build a single network rule dict."""
    ep: dict = {"host": host, "port": port, "protocol": protocol}
    if l7_rules:
        ep["rules"] = l7_rules
    rule: dict = {"name": host, "endpoints": [ep]}
    if binaries is not None:
        rule["binaries"] = binaries
    else:
        rule["binaries"] = []
    return rule


# ---------------------------------------------------------------------------
# can_exfiltrate
# ---------------------------------------------------------------------------


class TestCanExfiltrate:
    """Tests for the can_exfiltrate query."""

    def test_vulnerable_wildcard_host(self, svc: ProverService) -> None:
        policy = _simple_policy(
            network_policies={
                "evil": _net_rule("*.evil.com"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "*.evil.com"}},
            ],
        )
        assert len(results) == 1
        r = results[0]
        assert r["verdict"] == "VULNERABLE"
        assert r["satisfiable"] is True
        assert r["counterexample"] is not None
        assert r["counterexample"]["host"].endswith("evil.com")

    def test_safe_no_matching_host(self, svc: ProverService) -> None:
        policy = _simple_policy(
            network_policies={
                "docker": _net_rule("registry-1.docker.io"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "*.evil.com"}},
            ],
        )
        assert results[0]["verdict"] == "SAFE"
        assert results[0]["satisfiable"] is False
        assert results[0]["counterexample"] is None

    def test_exact_host_exfiltration(self, svc: ProverService) -> None:
        policy = _simple_policy(
            network_policies={
                "evil": _net_rule("evil.com"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "evil.com"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"

    def test_missing_param(self, svc: ProverService) -> None:
        policy = _simple_policy(network_policies={})
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {}},
            ],
        )
        assert results[0]["verdict"] == "ERROR"

    def test_empty_policy_is_safe(self, svc: ProverService) -> None:
        policy = _simple_policy(network_policies={})
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "*.evil.com"}},
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_wildcard_match_bare_domain(self, svc: ProverService) -> None:
        """*.evil.com should match evil.com itself (bare domain)."""
        policy = _simple_policy(
            network_policies={
                "rule": _net_rule("evil.com"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "*.evil.com"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"


# ---------------------------------------------------------------------------
# unrestricted_egress
# ---------------------------------------------------------------------------


class TestUnrestrictedEgress:
    """Tests for the unrestricted_egress query."""

    def test_wildcard_host_is_unrestricted(self, svc: ProverService) -> None:
        policy = _simple_policy(
            network_policies={
                "allow_all": _net_rule("*.example.com"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "unrestricted_egress"},
            ],
        )
        # Wildcard host means traffic can reach hosts outside the explicit list
        assert results[0]["verdict"] == "VULNERABLE"

    def test_locked_down_policy(self, svc: ProverService) -> None:
        policy = _simple_policy(
            network_policies={
                "docker": _net_rule("registry-1.docker.io"),
                "pypi": _net_rule("pypi.org"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "unrestricted_egress"},
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_empty_policy(self, svc: ProverService) -> None:
        policy = _simple_policy(network_policies={})
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "unrestricted_egress"},
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_mixed_wildcard_and_exact(self, svc: ProverService) -> None:
        """A mix of exact and wildcard rules — wildcard makes it unrestricted."""
        policy = _simple_policy(
            network_policies={
                "docker": _net_rule("registry-1.docker.io"),
                "wide": _net_rule("*.cloud.example.com"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "unrestricted_egress"},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"


# ---------------------------------------------------------------------------
# binary_bypass
# ---------------------------------------------------------------------------


class TestBinaryBypass:
    """Tests for the binary_bypass query."""

    def test_unrestricted_rule_allows_bypass(self, svc: ProverService) -> None:
        """Rule with no binary restriction lets any binary through."""
        policy = _simple_policy(
            network_policies={
                "open_rule": _net_rule("registry-1.docker.io", binaries=[]),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "binary_bypass", "params": {"binary_path": "/usr/bin/curl"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
        assert results[0]["counterexample"]["binary"] == "/usr/bin/curl"

    def test_restricted_rules_only(self, svc: ProverService) -> None:
        """All rules restrict to specific binaries — no bypass for others."""
        policy = _simple_policy(
            network_policies={
                "python_only": _net_rule(
                    "registry-1.docker.io",
                    binaries=[{"path": "/usr/bin/python3"}],
                ),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "binary_bypass", "params": {"binary_path": "/usr/bin/curl"}},
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_missing_param(self, svc: ProverService) -> None:
        policy = _simple_policy(network_policies={})
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "binary_bypass", "params": {}},
            ],
        )
        assert results[0]["verdict"] == "ERROR"

    def test_empty_policy_no_bypass(self, svc: ProverService) -> None:
        policy = _simple_policy(network_policies={})
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "binary_bypass", "params": {"binary_path": "/usr/bin/curl"}},
            ],
        )
        assert results[0]["verdict"] == "SAFE"


# ---------------------------------------------------------------------------
# write_despite_readonly
# ---------------------------------------------------------------------------


class TestWriteDespiteReadonly:
    """Tests for the write_despite_readonly query."""

    def test_no_overlap(self, svc: ProverService) -> None:
        """read_only paths with no matching read_write — safe."""
        policy = _simple_policy(
            filesystem={
                "read_only": ["/etc", "/usr"],
                "read_write": ["/tmp"],
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "write_despite_readonly"},
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_overlap_is_vulnerable(self, svc: ProverService) -> None:
        """A path in both read_only and read_write — writes to ro path possible."""
        policy = _simple_policy(
            filesystem={
                "read_only": ["/data"],
                "read_write": ["/data"],
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "write_despite_readonly"},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"

    def test_workdir_overlap(self, svc: ProverService) -> None:
        """include_workdir=True with /workdir in read_only."""
        policy = _simple_policy(
            filesystem={
                "include_workdir": True,
                "read_only": ["/workdir/sensitive"],
                "read_write": [],
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "write_despite_readonly"},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
        assert "workdir" in results[0]["counterexample"]["fs_path"]

    def test_no_readonly_paths(self, svc: ProverService) -> None:
        """No read_only paths — nothing to violate."""
        policy = _simple_policy(
            filesystem={
                "read_only": [],
                "read_write": ["/tmp"],
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "write_despite_readonly"},
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_empty_filesystem(self, svc: ProverService) -> None:
        policy = _simple_policy(filesystem={})
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "write_despite_readonly"},
            ],
        )
        assert results[0]["verdict"] == "SAFE"


# ---------------------------------------------------------------------------
# General / edge cases
# ---------------------------------------------------------------------------


class TestGeneralBehaviour:
    """Cross-cutting prover tests."""

    def test_unknown_query_id(self, svc: ProverService) -> None:
        results = svc.verify_policy({}, [{"query_id": "nonexistent"}])
        assert results[0]["verdict"] == "ERROR"

    def test_multiple_queries(self, svc: ProverService) -> None:
        policy = _simple_policy(
            network_policies={
                "docker": _net_rule("registry-1.docker.io"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "*.evil.com"}},
                {"query_id": "unrestricted_egress"},
            ],
        )
        assert len(results) == 2
        assert results[0]["verdict"] == "SAFE"
        assert results[1]["verdict"] == "SAFE"

    def test_timing_present(self, svc: ProverService) -> None:
        policy = _simple_policy(network_policies={})
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "unrestricted_egress"},
            ],
        )
        assert results[0]["z3_time_ms"] >= 0

    def test_empty_queries_list(self, svc: ProverService) -> None:
        results = svc.verify_policy({}, [])
        assert results == []

    def test_no_policy_key(self, svc: ProverService) -> None:
        """Policy dict with no network_policies key — should not crash."""
        results = svc.verify_policy(
            {},
            [
                {"query_id": "unrestricted_egress"},
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_counterexample_has_matched_rule(self, svc: ProverService) -> None:
        """Counterexample should include the rule key that matched."""
        policy = _simple_policy(
            network_policies={
                "evil_rule": _net_rule("evil.com"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "evil.com"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
        assert results[0]["counterexample"]["matched_rule"] == "evil_rule"

    def test_port_in_counterexample(self, svc: ProverService) -> None:
        """Counterexample should include the port number."""
        policy = _simple_policy(
            network_policies={
                "evil": _net_rule("evil.com", port=8080),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "evil.com"}},
            ],
        )
        assert results[0]["counterexample"]["port"] == 8080


class TestL7Rules:
    """Tests for L7 (HTTP method/path) rule encoding."""

    def test_l7_method_restriction(self, svc: ProverService) -> None:
        """Rule with L7 GET-only restriction should not allow POST."""
        policy = _simple_policy(
            network_policies={
                "api": _net_rule(
                    "api.example.com",
                    l7_rules=[{"allow": {"method": "GET", "path": "/**"}}],
                ),
            }
        )
        # The rule allows traffic to api.example.com, so exfiltration is possible
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "api.example.com"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
        assert results[0]["counterexample"]["method"] == "GET"

    def test_l7_path_restriction(self, svc: ProverService) -> None:
        """Rule with L7 path restriction encodes correctly."""
        policy = _simple_policy(
            network_policies={
                "api": _net_rule(
                    "api.example.com",
                    l7_rules=[{"allow": {"method": "GET", "path": "/api/v1/**"}}],
                ),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "api.example.com"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
        ce = results[0]["counterexample"]
        # Path should start with /api/v1
        assert ce["path"].startswith("/api/v1")

    def test_multi_rule_policy(self, svc: ProverService) -> None:
        """Multiple network rules — exfiltration check should find a match."""
        policy = _simple_policy(
            network_policies={
                "docker": _net_rule("registry-1.docker.io"),
                "pypi": _net_rule("pypi.org"),
                "evil": _net_rule("evil.com"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "evil.com"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
        assert results[0]["counterexample"]["matched_rule"] == "evil"

    def test_binary_restricted_rule(self, svc: ProverService) -> None:
        """Binary-restricted rule should only match the listed binary."""
        policy = _simple_policy(
            network_policies={
                "python_api": _net_rule(
                    "api.example.com",
                    binaries=[{"path": "/usr/bin/python3"}],
                ),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {"query_id": "can_exfiltrate", "params": {"host_pattern": "api.example.com"}},
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
        assert results[0]["counterexample"]["binary"] == "/usr/bin/python3"


# ---------------------------------------------------------------------------
# deny rules (upstream #822)
# ---------------------------------------------------------------------------


class TestDenyRulesInProver:
    """Deny rules override allow rules in the prover encoding."""

    def _rule_with_deny(self, host: str, allow_path: str, deny_path: str) -> dict:
        return {
            "name": host,
            "endpoints": [
                {
                    "host": host,
                    "port": 443,
                    "protocol": "rest",
                    "rules": [{"allow": {"method": "GET", "path": allow_path}}],
                    "deny_rules": [{"method": "GET", "path": deny_path}],
                }
            ],
            "binaries": [],
        }

    def test_deny_blocks_otherwise_allowed_path(self, svc: ProverService) -> None:
        """A deny rule identical to the allow rule should make exfiltration SAFE."""
        policy = _simple_policy(
            network_policies={
                "api": self._rule_with_deny("api.example.com", "/v1/**", "/v1/**"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {
                    "query_id": "can_exfiltrate",
                    "params": {"host_pattern": "api.example.com"},
                },
            ],
        )
        assert results[0]["verdict"] == "SAFE"

    def test_partial_deny_still_vulnerable(self, svc: ProverService) -> None:
        """Deny rule narrower than allow should still leave the endpoint reachable."""
        policy = _simple_policy(
            network_policies={
                "api": self._rule_with_deny("api.example.com", "/v1/**", "/v1/admin/**"),
            }
        )
        results = svc.verify_policy(
            policy,
            [
                {
                    "query_id": "can_exfiltrate",
                    "params": {"host_pattern": "api.example.com"},
                },
            ],
        )
        assert results[0]["verdict"] == "VULNERABLE"
