"""Unit tests for shoreguard.services.policy_diff."""
# pyright: reportTypedDictNotRequiredAccess=false

from __future__ import annotations

from shoreguard.services.policy_diff import diff_policy, is_empty, summary


def test_empty_dicts():
    d = diff_policy({}, {})
    assert is_empty(d)
    assert summary(d)["total"] == 0


def test_none_inputs():
    d = diff_policy(None, None)
    assert is_empty(d)


def test_filesystem_add_remove():
    old = {"filesystem": {"read_only": ["/usr"], "read_write": ["/tmp"]}}
    new = {"filesystem": {"read_only": ["/usr", "/etc"], "read_write": []}}
    d = diff_policy(old, new)
    assert d["filesystem"]["read_only_added"] == ["/etc"]
    assert d["filesystem"]["read_write_removed"] == ["/tmp"]
    assert "read_only_removed" not in d["filesystem"]
    assert not is_empty(d)


def test_filesystem_include_workdir_toggle():
    old = {"filesystem": {"include_workdir": False}}
    new = {"filesystem": {"include_workdir": True}}
    d = diff_policy(old, new)
    assert d["filesystem"]["include_workdir_changed"] == (False, True)


def test_process_changes():
    old = {"process": {"run_as_user": "root", "run_as_group": "root"}}
    new = {"process": {"run_as_user": "app", "run_as_group": "root"}}
    d = diff_policy(old, new)
    assert d["process"]["run_as_user_changed"] == ("root", "app")
    assert "run_as_group_changed" not in d["process"]


def test_network_added_removed_changed():
    old = {
        "network_policies": {
            "anthropic": {"name": "anthropic", "endpoints": [{"host": "api.anthropic.com"}]},
            "internal": {"name": "internal"},
        }
    }
    new = {
        "network_policies": {
            "anthropic": {
                "name": "anthropic",
                "endpoints": [{"host": "api.anthropic.com", "port": 443}],
            },
            "openai": {"name": "openai"},
        }
    }
    d = diff_policy(old, new)
    assert d["network_policies"]["added"] == ["openai"]
    assert d["network_policies"]["removed"] == ["internal"]
    assert d["network_policies"]["changed"] == ["anthropic"]


def test_summary_counts():
    old = {"filesystem": {"read_only": ["/usr"]}, "network_policies": {"a": {}}}
    new = {"filesystem": {"read_only": ["/etc"]}, "network_policies": {"b": {}}}
    d = diff_policy(old, new)
    s = summary(d)
    assert s["filesystem"] == 2  # one added + one removed
    assert s["network_policies"] == 2
    assert s["total"] == 4


def test_deterministic_ordering():
    new = {
        "filesystem": {"read_only": ["/z", "/a", "/m"]},
        "network_policies": {"z": {}, "a": {}, "m": {}},
    }
    d = diff_policy({}, new)
    assert d["filesystem"]["read_only_added"] == ["/a", "/m", "/z"]
    assert d["network_policies"]["added"] == ["a", "m", "z"]
