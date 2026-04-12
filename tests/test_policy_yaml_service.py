"""Unit tests for shoreguard.services.policy_yaml."""

from __future__ import annotations

import datetime

import pytest

from shoreguard.services.policy_yaml import (
    PolicyYamlError,
    parse_yaml,
    render_yaml,
    yaml_fingerprint,
)


def _sample_policy():
    return {
        "version": 7,
        "filesystem": {"include_workdir": True, "read_only": ["/usr"], "read_write": ["/tmp"]},
        "process": {"run_as_user": "app", "run_as_group": "app"},
        "network_policies": {
            "anthropic": {
                "name": "anthropic",
                "endpoints": [{"host": "api.anthropic.com", "port": 443}],
                "binaries": [],
            }
        },
    }


def test_render_includes_header_and_metadata():
    text = render_yaml(
        _sample_policy(),
        gateway="prod",
        sandbox="web-api",
        version=7,
        policy_hash="sha256:abc",
        exported_at=datetime.datetime(2026, 4, 12, tzinfo=datetime.UTC),
    )
    assert text.startswith("# managed-by: shoreguard-gitops\n")
    assert "gateway: prod" in text
    assert "sandbox: web-api" in text
    assert "version: 7" in text
    assert "policy_hash: sha256:abc" in text
    assert "exported_at: '2026-04-12T00:00:00+00:00'" in text


def test_render_is_deterministic():
    a = render_yaml(
        _sample_policy(),
        gateway="g",
        sandbox="s",
        exported_at=datetime.datetime(2026, 4, 12, tzinfo=datetime.UTC),
    )
    b = render_yaml(
        _sample_policy(),
        gateway="g",
        sandbox="s",
        exported_at=datetime.datetime(2026, 4, 12, tzinfo=datetime.UTC),
    )
    assert a == b


def test_round_trip_byte_identical():
    p = _sample_policy()
    rendered = render_yaml(
        p,
        gateway="g",
        sandbox="s",
        version=7,
        policy_hash="sha256:abc",
        exported_at=datetime.datetime(2026, 4, 12, tzinfo=datetime.UTC),
    )
    parsed_policy, parsed_meta = parse_yaml(rendered)
    rendered2 = render_yaml(
        parsed_policy,
        gateway=parsed_meta["gateway"],
        sandbox=parsed_meta["sandbox"],
        version=parsed_meta.get("version"),
        policy_hash=parsed_meta.get("policy_hash"),
        exported_at=datetime.datetime.fromisoformat(parsed_meta["exported_at"]),
    )
    assert rendered == rendered2


def test_parse_malformed_yaml():
    with pytest.raises(PolicyYamlError, match="Malformed"):
        parse_yaml("key: : :")


def test_parse_missing_policy_key():
    with pytest.raises(PolicyYamlError, match="Missing required 'policy'"):
        parse_yaml("metadata: {gateway: g}\n")


def test_parse_non_mapping_top_level():
    with pytest.raises(PolicyYamlError, match="must be a mapping"):
        parse_yaml("- 1\n- 2\n")


def test_parse_no_metadata_block_ok():
    policy, metadata = parse_yaml("policy:\n  filesystem:\n    read_only: [/usr]\n")
    assert policy["filesystem"]["read_only"] == ["/usr"]
    assert metadata == {}


def test_yaml_fingerprint_stable_and_short():
    fp = yaml_fingerprint("policy: {}\n")
    assert len(fp) == 16
    assert fp == yaml_fingerprint("policy: {}\n")
    assert fp != yaml_fingerprint("policy: {filesystem: {}}\n")
