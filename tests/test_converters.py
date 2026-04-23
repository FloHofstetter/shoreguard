"""Tests for protobuf dict→proto conversion helpers."""

from __future__ import annotations

import os

import pytest

from shoreguard.client._converters import (
    PolicyValidationError,
    _dict_to_l7_query,
    _dict_to_network_rule,
    _dict_to_policy,
)

# ---------- _dict_to_policy ----------


def test_dict_to_policy_empty():
    policy = _dict_to_policy({})
    assert policy.version == 0


def test_dict_to_policy_sets_version():
    policy = _dict_to_policy({"version": 5})
    assert policy.version == 5


def test_dict_to_policy_filesystem_key():
    policy = _dict_to_policy(
        {
            "filesystem": {
                "include_workdir": True,
                "read_only": ["/usr", "/etc"],
                "read_write": ["/tmp"],
            },
        }
    )
    assert policy.filesystem.include_workdir is True
    assert list(policy.filesystem.read_only) == ["/usr", "/etc"]
    assert list(policy.filesystem.read_write) == ["/tmp"]


def test_dict_to_policy_filesystem_include_workdir_false():
    policy = _dict_to_policy(
        {
            "filesystem": {"include_workdir": False},
        }
    )
    assert policy.filesystem.include_workdir is False


def test_dict_to_policy_filesystem_policy_key():
    """Accepts 'filesystem_policy' as alternative key."""
    policy = _dict_to_policy(
        {
            "filesystem_policy": {"read_only": ["/usr"], "read_write": ["/tmp"]},
        }
    )
    assert list(policy.filesystem.read_only) == ["/usr"]
    assert list(policy.filesystem.read_write) == ["/tmp"]


def test_dict_to_policy_filesystem_prefers_filesystem_over_filesystem_policy():
    """When both keys present, 'filesystem' wins."""
    policy = _dict_to_policy(
        {
            "filesystem": {"read_only": ["/a"]},
            "filesystem_policy": {"read_only": ["/b"]},
        }
    )
    assert list(policy.filesystem.read_only) == ["/a"]


def test_dict_to_policy_filesystem_defaults():
    policy = _dict_to_policy({"filesystem": {}})
    assert policy.filesystem.include_workdir is False
    assert list(policy.filesystem.read_only) == []
    assert list(policy.filesystem.read_write) == []


def test_dict_to_policy_process():
    policy = _dict_to_policy(
        {
            "process": {"run_as_user": "nobody", "run_as_group": "nogroup"},
        }
    )
    assert policy.process.run_as_user == "nobody"
    assert policy.process.run_as_group == "nogroup"


def test_dict_to_policy_process_defaults():
    policy = _dict_to_policy({"process": {}})
    assert policy.process.run_as_user == ""
    assert policy.process.run_as_group == ""


def test_dict_to_policy_landlock():
    policy = _dict_to_policy(
        {
            "landlock": {"compatibility": "best_effort"},
        }
    )
    assert policy.landlock.compatibility == "best_effort"


def test_dict_to_policy_landlock_defaults():
    policy = _dict_to_policy({"landlock": {}})
    assert policy.landlock.compatibility == ""


def test_dict_to_policy_network_policies():
    policy = _dict_to_policy(
        {
            "network_policies": {
                "allow-dns": {
                    "name": "allow-dns",
                    "endpoints": [{"host": "8.8.8.8", "port": 53, "protocol": "udp"}],
                    "binaries": [],
                },
                "allow-https": {
                    "name": "allow-https",
                    "endpoints": [
                        {"host": "example.com", "port": 443, "protocol": "tcp", "tls": "required"},
                    ],
                    "binaries": [{"path": "/usr/bin/curl"}],
                },
            },
        }
    )
    assert "allow-dns" in policy.network_policies
    assert "allow-https" in policy.network_policies
    dns_rule = policy.network_policies["allow-dns"]
    assert dns_rule.name == "allow-dns"
    assert len(dns_rule.endpoints) == 1
    assert dns_rule.endpoints[0].host == "8.8.8.8"
    assert dns_rule.endpoints[0].port == 53
    assert dns_rule.endpoints[0].protocol == "udp"

    https_rule = policy.network_policies["allow-https"]
    assert https_rule.name == "allow-https"
    assert https_rule.endpoints[0].host == "example.com"
    assert https_rule.endpoints[0].port == 443
    assert https_rule.endpoints[0].tls == "required"
    assert len(https_rule.binaries) == 1
    assert https_rule.binaries[0].path == "/usr/bin/curl"


def test_dict_to_policy_full():
    policy = _dict_to_policy(
        {
            "version": 3,
            "filesystem": {
                "include_workdir": True,
                "read_only": ["/usr"],
                "read_write": ["/tmp"],
            },
            "process": {"run_as_user": "app", "run_as_group": "app"},
            "landlock": {"compatibility": "best_effort"},
            "network_policies": {
                "rule1": {"name": "rule1", "endpoints": [], "binaries": []},
            },
        }
    )
    assert policy.version == 3
    assert policy.filesystem.include_workdir is True
    assert list(policy.filesystem.read_only) == ["/usr"]
    assert list(policy.filesystem.read_write) == ["/tmp"]
    assert policy.process.run_as_user == "app"
    assert policy.process.run_as_group == "app"
    assert policy.landlock.compatibility == "best_effort"
    assert "rule1" in policy.network_policies


# ---------- _dict_to_network_rule ----------


def test_dict_to_network_rule_empty():
    rule = _dict_to_network_rule({})
    assert rule.name == ""
    assert len(rule.endpoints) == 0
    assert len(rule.binaries) == 0


def test_dict_to_network_rule_name():
    rule = _dict_to_network_rule({"name": "my-rule"})
    assert rule.name == "my-rule"


def test_dict_to_network_rule_with_binaries():
    rule = _dict_to_network_rule(
        {
            "name": "test",
            "endpoints": [],
            "binaries": [{"path": "/usr/bin/curl"}, {"path": "/usr/bin/wget"}],
        }
    )
    assert len(rule.binaries) == 2
    assert rule.binaries[0].path == "/usr/bin/curl"
    assert rule.binaries[1].path == "/usr/bin/wget"


def test_dict_to_network_rule_binary_defaults():
    rule = _dict_to_network_rule({"binaries": [{}]})
    assert rule.binaries[0].path == ""


def test_dict_to_network_rule_endpoint_all_fields():
    rule = _dict_to_network_rule(
        {
            "endpoints": [
                {
                    "host": "api.example.com",
                    "port": 443,
                    "protocol": "tcp",
                    "tls": "required",
                    "enforcement": "block",
                    "access": "allow",
                    "allowed_ips": ["10.0.0.1", "10.0.0.2"],
                    "ports": [80, 443, 8080],
                }
            ],
        }
    )
    ep = rule.endpoints[0]
    assert ep.host == "api.example.com"
    assert ep.port == 443
    assert ep.protocol == "tcp"
    assert ep.tls == "required"
    assert ep.enforcement == "block"
    assert ep.access == "allow"
    assert list(ep.allowed_ips) == ["10.0.0.1", "10.0.0.2"]
    assert list(ep.ports) == [80, 443, 8080]


def test_dict_to_network_rule_endpoint_defaults():
    rule = _dict_to_network_rule({"endpoints": [{}]})
    ep = rule.endpoints[0]
    assert ep.host == ""
    assert ep.port == 0
    assert ep.protocol == ""
    assert ep.tls == ""
    assert ep.enforcement == ""
    assert ep.access == ""
    assert list(ep.allowed_ips) == []
    assert list(ep.ports) == []
    assert ep.allow_encoded_slash is False


def test_dict_to_network_rule_allow_encoded_slash_true():
    rule = _dict_to_network_rule(
        {
            "endpoints": [
                {
                    "host": "gitlab.example.com",
                    "port": 443,
                    "allow_encoded_slash": True,
                }
            ],
        }
    )
    assert rule.endpoints[0].allow_encoded_slash is True


def test_dict_to_network_rule_allow_encoded_slash_default_false():
    rule = _dict_to_network_rule({"endpoints": [{"host": "api.example.com", "port": 443}]})
    assert rule.endpoints[0].allow_encoded_slash is False


def test_dict_to_network_rule_multiple_endpoints():
    rule = _dict_to_network_rule(
        {
            "endpoints": [
                {"host": "a.com", "port": 80},
                {"host": "b.com", "port": 443},
            ],
        }
    )
    assert len(rule.endpoints) == 2
    assert rule.endpoints[0].host == "a.com"
    assert rule.endpoints[0].port == 80
    assert rule.endpoints[1].host == "b.com"
    assert rule.endpoints[1].port == 443


def test_dict_to_network_rule_l7_rules():
    rule = _dict_to_network_rule(
        {
            "endpoints": [
                {
                    "host": "api.example.com",
                    "port": 443,
                    "rules": [
                        {"allow": {"method": "GET", "path": "/health"}},
                        {"allow": {"method": "POST", "path": "/data", "command": "curl"}},
                    ],
                }
            ],
        }
    )
    ep = rule.endpoints[0]
    assert len(ep.rules) == 2
    assert ep.rules[0].allow.method == "GET"
    assert ep.rules[0].allow.path == "/health"
    assert ep.rules[0].allow.command == ""
    assert ep.rules[1].allow.method == "POST"
    assert ep.rules[1].allow.path == "/data"
    assert ep.rules[1].allow.command == "curl"


def test_dict_to_network_rule_l7_rule_defaults():
    rule = _dict_to_network_rule(
        {
            "endpoints": [{"rules": [{"allow": {}}]}],
        }
    )
    allow = rule.endpoints[0].rules[0].allow
    assert allow.method == ""
    assert allow.path == ""
    assert allow.command == ""


def test_dict_to_network_rule_l7_rule_missing_allow():
    rule = _dict_to_network_rule(
        {
            "endpoints": [{"rules": [{}]}],
        }
    )
    allow = rule.endpoints[0].rules[0].allow
    assert allow.method == ""
    assert allow.path == ""
    assert allow.command == ""


def test_dict_to_network_rule_full():
    rule = _dict_to_network_rule(
        {
            "name": "full-rule",
            "endpoints": [
                {
                    "host": "api.example.com",
                    "port": 443,
                    "protocol": "tcp",
                    "tls": "required",
                    "enforcement": "block",
                    "access": "allow",
                    "allowed_ips": ["10.0.0.0/8"],
                    "ports": [443],
                    "rules": [{"allow": {"method": "GET", "path": "/api"}}],
                },
            ],
            "binaries": [{"path": "/usr/bin/curl"}],
        }
    )
    assert rule.name == "full-rule"
    assert len(rule.endpoints) == 1
    assert len(rule.binaries) == 1
    ep = rule.endpoints[0]
    assert ep.host == "api.example.com"
    assert ep.port == 443
    assert ep.protocol == "tcp"
    assert ep.tls == "required"
    assert ep.enforcement == "block"
    assert ep.access == "allow"
    assert list(ep.allowed_ips) == ["10.0.0.0/8"]
    assert list(ep.ports) == [443]
    assert ep.rules[0].allow.method == "GET"
    assert ep.rules[0].allow.path == "/api"
    assert rule.binaries[0].path == "/usr/bin/curl"


def test_dict_to_network_rule_l7_query_matchers():
    """L7Allow with query parameter matchers converts correctly."""
    rule = _dict_to_network_rule(
        {
            "endpoints": [
                {
                    "host": "api.example.com",
                    "port": 443,
                    "rules": [
                        {
                            "allow": {
                                "method": "GET",
                                "path": "/v1/models",
                                "query": {
                                    "token": {"glob": "sk-*"},
                                    "format": {"any": ["json", "xml"]},
                                },
                            }
                        },
                    ],
                }
            ],
        }
    )
    allow = rule.endpoints[0].rules[0].allow
    assert allow.method == "GET"
    assert "token" in allow.query
    assert allow.query["token"].glob == "sk-*"
    assert "format" in allow.query
    assert list(allow.query["format"].any) == ["json", "xml"]


def test_dict_to_network_rule_l7_no_query():
    """L7Allow without query field produces empty query map."""
    rule = _dict_to_network_rule(
        {
            "endpoints": [
                {
                    "host": "example.com",
                    "rules": [{"allow": {"method": "GET", "path": "/"}}],
                }
            ],
        }
    )
    allow = rule.endpoints[0].rules[0].allow
    assert len(allow.query) == 0


# ─── Mutation-killing tests: _dict_to_policy ────────────────────────────────


class TestDictToPolicyMutations:
    """Kill mutations in _dict_to_policy edge cases."""

    def test_filesystem_policy_key_fallback(self):
        """When 'filesystem' key is falsy (empty dict), filesystem_policy is used."""
        # 'filesystem' key present but falsy -> uses filesystem_policy
        policy = _dict_to_policy(
            {"filesystem_policy": {"include_workdir": True, "read_only": ["/x"]}}
        )
        assert policy.filesystem.include_workdir is True
        assert list(policy.filesystem.read_only) == ["/x"]

    def test_no_filesystem_no_filesystem_policy(self):
        """Neither key present -> no filesystem field set."""
        policy = _dict_to_policy({"version": 1})
        assert not policy.HasField("filesystem")

    def test_no_process_no_field(self):
        policy = _dict_to_policy({"version": 1})
        assert not policy.HasField("process")

    def test_no_landlock_no_field(self):
        policy = _dict_to_policy({"version": 1})
        assert not policy.HasField("landlock")

    def test_no_network_policies_empty_map(self):
        policy = _dict_to_policy({"version": 1})
        assert len(policy.network_policies) == 0

    def test_version_zero_when_missing(self):
        policy = _dict_to_policy({})
        assert policy.version == 0

    def test_filesystem_read_only_empty_list(self):
        policy = _dict_to_policy({"filesystem": {"read_only": []}})
        assert list(policy.filesystem.read_only) == []

    def test_filesystem_read_write_empty_list(self):
        policy = _dict_to_policy({"filesystem": {"read_write": []}})
        assert list(policy.filesystem.read_write) == []

    def test_process_run_as_user_empty_default(self):
        policy = _dict_to_policy({"process": {}})
        assert policy.process.run_as_user == ""

    def test_process_run_as_group_empty_default(self):
        policy = _dict_to_policy({"process": {}})
        assert policy.process.run_as_group == ""

    def test_landlock_compatibility_empty_default(self):
        policy = _dict_to_policy({"landlock": {}})
        assert policy.landlock.compatibility == ""

    def test_multiple_network_rules(self):
        policy = _dict_to_policy(
            {
                "network_policies": {
                    "r1": {"name": "r1", "endpoints": [], "binaries": []},
                    "r2": {"name": "r2", "endpoints": [], "binaries": []},
                }
            }
        )
        assert len(policy.network_policies) == 2
        assert policy.network_policies["r1"].name == "r1"
        assert policy.network_policies["r2"].name == "r2"


class TestDictToNetworkRuleMutations:
    """Kill mutations in _dict_to_network_rule edge cases."""

    def test_endpoint_host_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert rule.endpoints[0].host == ""

    def test_endpoint_port_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert rule.endpoints[0].port == 0

    def test_endpoint_protocol_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert rule.endpoints[0].protocol == ""

    def test_endpoint_tls_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert rule.endpoints[0].tls == ""

    def test_endpoint_enforcement_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert rule.endpoints[0].enforcement == ""

    def test_endpoint_access_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert rule.endpoints[0].access == ""

    def test_endpoint_allowed_ips_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert list(rule.endpoints[0].allowed_ips) == []

    def test_endpoint_ports_default(self):
        rule = _dict_to_network_rule({"endpoints": [{}]})
        assert list(rule.endpoints[0].ports) == []

    def test_rule_allow_missing_uses_empty(self):
        """When rule dict has no 'allow' key, defaults to empty L7Allow."""
        rule = _dict_to_network_rule({"endpoints": [{"rules": [{}]}]})
        allow = rule.endpoints[0].rules[0].allow
        assert allow.method == ""
        assert allow.path == ""
        assert allow.command == ""

    def test_rule_allow_partial_fields(self):
        rule = _dict_to_network_rule({"endpoints": [{"rules": [{"allow": {"method": "POST"}}]}]})
        allow = rule.endpoints[0].rules[0].allow
        assert allow.method == "POST"
        assert allow.path == ""
        assert allow.command == ""

    def test_binary_path_preserved(self):
        # Use paths that are guaranteed not to exist locally so the new
        # symlink-resolution pass in _dict_to_network_rule stays a no-op.
        rule = _dict_to_network_rule(
            {
                "binaries": [
                    {"path": "/sg-test/bin/alpha"},
                    {"path": "/sg-test/bin/beta"},
                ]
            }
        )
        assert rule.binaries[0].path == "/sg-test/bin/alpha"
        assert rule.binaries[1].path == "/sg-test/bin/beta"

    def test_name_default_empty(self):
        rule = _dict_to_network_rule({"endpoints": []})
        assert rule.name == ""

    def test_no_endpoints_key(self):
        rule = _dict_to_network_rule({"name": "test"})
        assert len(rule.endpoints) == 0

    def test_no_binaries_key(self):
        rule = _dict_to_network_rule({"name": "test"})
        assert len(rule.binaries) == 0


class TestDictToL7QueryMutations:
    """Kill mutations in _dict_to_l7_query."""

    def test_empty_dict(self):
        result = _dict_to_l7_query({})
        assert result == {}

    def test_glob_only(self):
        result = _dict_to_l7_query({"param": {"glob": "*.json"}})
        assert result["param"].glob == "*.json"
        assert list(result["param"].any) == []

    def test_any_only(self):
        result = _dict_to_l7_query({"param": {"any": ["a", "b"]}})
        assert result["param"].glob == ""
        assert list(result["param"].any) == ["a", "b"]

    def test_both_glob_and_any(self):
        result = _dict_to_l7_query({"p": {"glob": "g*", "any": ["x"]}})
        assert result["p"].glob == "g*"
        assert list(result["p"].any) == ["x"]

    def test_multiple_keys(self):
        result = _dict_to_l7_query(
            {
                "a": {"glob": "a*"},
                "b": {"any": ["1", "2"]},
            }
        )
        assert "a" in result
        assert "b" in result
        assert result["a"].glob == "a*"
        assert list(result["b"].any) == ["1", "2"]

    def test_missing_glob_key_defaults_empty(self):
        result = _dict_to_l7_query({"p": {"any": ["x"]}})
        assert result["p"].glob == ""

    def test_missing_any_key_defaults_empty(self):
        result = _dict_to_l7_query({"p": {"glob": "g"}})
        assert list(result["p"].any) == []

    def test_empty_matcher(self):
        result = _dict_to_l7_query({"p": {}})
        assert result["p"].glob == ""
        assert list(result["p"].any) == []


class TestDictToNetworkRuleL7WithQuery:
    """Test L7 rules with query through _dict_to_network_rule."""

    def test_query_preserved_in_endpoint_rule(self):
        rule = _dict_to_network_rule(
            {
                "endpoints": [
                    {
                        "host": "example.com",
                        "rules": [
                            {
                                "allow": {
                                    "method": "GET",
                                    "path": "/api",
                                    "query": {
                                        "token": {"glob": "tk-*"},
                                    },
                                }
                            }
                        ],
                    }
                ],
            }
        )
        allow = rule.endpoints[0].rules[0].allow
        assert "token" in allow.query
        assert allow.query["token"].glob == "tk-*"

    def test_no_query_key_in_allow(self):
        rule = _dict_to_network_rule(
            {
                "endpoints": [
                    {
                        "rules": [{"allow": {"method": "GET"}}],
                    }
                ],
            }
        )
        allow = rule.endpoints[0].rules[0].allow
        assert len(allow.query) == 0


# ---------- M29: deny rules, TLD reject, symlink resolve ----------


class TestDenyRules:
    """Deny rules are preserved through the converter (upstream #822)."""

    def test_deny_rules_roundtrip_through_converter(self):
        rule = _dict_to_network_rule(
            {
                "endpoints": [
                    {
                        "host": "api.example.com",
                        "rules": [{"allow": {"method": "GET", "path": "/v1/**"}}],
                        "deny_rules": [
                            {"method": "DELETE", "path": "/v1/**"},
                            {"method": "POST", "path": "/v1/admin/**"},
                        ],
                    }
                ]
            }
        )
        ep = rule.endpoints[0]
        assert len(ep.rules) == 1
        assert len(ep.deny_rules) == 2
        assert ep.deny_rules[0].method == "DELETE"
        assert ep.deny_rules[0].path == "/v1/**"
        assert ep.deny_rules[1].method == "POST"

    def test_deny_rule_query_matcher(self):
        rule = _dict_to_network_rule(
            {
                "endpoints": [
                    {
                        "host": "api.example.com",
                        "deny_rules": [
                            {
                                "method": "GET",
                                "path": "/search",
                                "query": {"q": {"glob": "*secret*"}},
                            }
                        ],
                    }
                ]
            }
        )
        deny = rule.endpoints[0].deny_rules[0]
        assert "q" in deny.query
        assert deny.query["q"].glob == "*secret*"


class TestHostPatternValidation:
    """TLD-level wildcard rejection (upstream #791)."""

    @pytest.mark.parametrize("bad_host", ["*.com", "*.io", "*.net", "*.local"])
    def test_tld_wildcard_rejected(self, bad_host):
        with pytest.raises(PolicyValidationError, match="TLD level"):
            _dict_to_network_rule({"endpoints": [{"host": bad_host}]})

    @pytest.mark.parametrize(
        "ok_host",
        ["*.example.com", "api.example.com", "*.api.example.com", ""],
    )
    def test_multi_label_wildcard_allowed(self, ok_host):
        # No exception — the rule builds successfully.
        rule = _dict_to_network_rule({"endpoints": [{"host": ok_host}]})
        assert rule.endpoints[0].host == ok_host


class TestSymlinkResolution:
    """Binary paths follow local symlinks (upstream #774)."""

    def test_symlink_resolved_at_write_time(self, tmp_path):
        target = tmp_path / "real-bin"
        target.write_text("")
        link = tmp_path / "link-bin"
        os.symlink(target, link)

        rule = _dict_to_network_rule({"binaries": [{"path": str(link)}]})
        assert rule.binaries[0].path == str(target)

    def test_non_symlink_passthrough(self, tmp_path):
        real = tmp_path / "plain-bin"
        real.write_text("")
        rule = _dict_to_network_rule({"binaries": [{"path": str(real)}]})
        assert rule.binaries[0].path == str(real)

    def test_nonexistent_path_passthrough(self):
        rule = _dict_to_network_rule({"binaries": [{"path": "/sg-nonexistent/bin/x"}]})
        assert rule.binaries[0].path == "/sg-nonexistent/bin/x"
