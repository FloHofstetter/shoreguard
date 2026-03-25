"""Tests for protobuf dict→proto conversion helpers."""

from __future__ import annotations

from shoreguard.client._converters import _dict_to_network_rule, _dict_to_policy

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
