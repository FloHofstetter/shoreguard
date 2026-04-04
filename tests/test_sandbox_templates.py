"""Unit tests for sandbox template loading (pure functions, no gRPC)."""

from __future__ import annotations

from shoreguard.sandbox_templates import get_template, list_templates


def test_list_templates_returns_entries():
    """list_templates returns at least one bundled template."""
    result = list_templates()
    assert isinstance(result, list)
    assert len(result) >= 1
    for entry in result:
        assert "name" in entry
        assert "description" in entry
        assert "category" in entry
        assert "file" in entry


def test_list_templates_contains_web_dev():
    """The bundled web-dev template appears in the listing."""
    names = [t["name"] for t in list_templates()]
    assert "web-dev" in names


def test_get_template_existing():
    """get_template returns a valid dict for a known template."""
    result = get_template("web-dev")
    assert result is not None
    assert result["name"] == "web-dev"
    assert "description" in result
    assert "sandbox" in result
    assert isinstance(result["sandbox"], dict)


def test_get_template_nonexistent():
    """get_template returns None for an unknown template name."""
    assert get_template("nonexistent-template-xyz") is None


def test_get_template_path_traversal():
    """get_template rejects path traversal attempts."""
    assert get_template("../../etc/passwd") is None
    assert get_template("../../../etc/shadow") is None
