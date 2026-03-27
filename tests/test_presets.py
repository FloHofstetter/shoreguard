"""Tests for policy preset loading."""

from __future__ import annotations

from shoreguard.presets import _PRESETS_DIR, get_preset, list_presets


def test_list_presets_returns_list():
    """list_presets() returns list with name/description/file for each preset."""
    presets = list_presets()
    assert isinstance(presets, list)
    assert len(presets) > 0
    for p in presets:
        assert "name" in p
        assert "description" in p
        assert "file" in p
        assert p["file"].endswith(".yaml")


def test_list_presets_dir_exists():
    """Preset dir exists and has YAML files."""
    assert _PRESETS_DIR.exists()
    yaml_files = list(_PRESETS_DIR.glob("*.yaml"))
    assert len(yaml_files) > 0


def test_get_preset_existing():
    """get_preset() returns PresetDetail format {name, description, policy}."""
    result = get_preset("pypi")
    assert result is not None
    assert result["name"] == "pypi"
    assert "description" in result
    assert "policy" in result
    assert "network_policies" in result["policy"]


def test_get_preset_nonexistent():
    """get_preset() for a nonexistent preset returns None."""
    result = get_preset("nonexistent-preset-xyz-12345")
    assert result is None


def test_list_presets_empty_dir(tmp_path, monkeypatch):
    """list_presets() returns empty list when preset dir doesn't exist."""
    monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", tmp_path / "nonexistent")
    result = list_presets()
    assert result == []


def test_list_presets_with_yaml_files(tmp_path, monkeypatch):
    """list_presets() reads YAML files from the preset directory."""
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir()
    (preset_dir / "test.yaml").write_text(
        "preset:\n  name: test-preset\n  description: A test preset\n"
    )
    monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
    result = list_presets()
    assert len(result) == 1
    assert result[0]["name"] == "test-preset"
    assert result[0]["description"] == "A test preset"
    assert result[0]["file"] == "test.yaml"


def test_get_preset_traversal():
    """Path traversal attempts return None."""
    assert get_preset("../../etc/passwd") is None
    assert get_preset("../../../etc/shadow") is None


def test_get_preset_malformed_yaml(tmp_path, monkeypatch):
    """get_preset() with malformed YAML returns None gracefully."""
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir()
    (preset_dir / "bad.yaml").write_text(": :")
    monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
    assert get_preset("bad") is None


def test_get_preset_empty_file(tmp_path, monkeypatch):
    """get_preset() for an empty YAML file returns None."""
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir()
    (preset_dir / "empty.yaml").write_text("")
    monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
    assert get_preset("empty") is None


def test_list_presets_skips_malformed(tmp_path, monkeypatch):
    """list_presets() skips malformed YAML files and returns the good ones."""
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir()
    (preset_dir / "good.yaml").write_text(
        "preset:\n  name: good-preset\n  description: Works fine\n"
    )
    (preset_dir / "bad.yaml").write_text(": :")
    monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
    result = list_presets()
    assert len(result) == 1
    assert result[0]["name"] == "good-preset"
