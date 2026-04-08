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


# ── Additional mutation-killing tests ────────────────────────────────────────


class TestListPresetsEdgeCases:
    def test_list_presets_non_dict_yaml_skipped(self, tmp_path, monkeypatch):
        """YAML files that parse to non-dict types should be skipped."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "list.yaml").write_text("- item1\n- item2\n")
        (preset_dir / "string.yaml").write_text("just a string\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert result == []

    def test_list_presets_missing_preset_key_uses_stem(self, tmp_path, monkeypatch):
        """When 'preset' key is missing, name defaults to file stem."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "mypreset.yaml").write_text("network_policies:\n  - allow: all\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert len(result) == 1
        assert result[0]["name"] == "mypreset"
        assert result[0]["description"] == ""
        assert result[0]["file"] == "mypreset.yaml"

    def test_list_presets_partial_preset_meta(self, tmp_path, monkeypatch):
        """preset key with only name but no description."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "partial.yaml").write_text("preset:\n  name: partial-only\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert len(result) == 1
        assert result[0]["name"] == "partial-only"
        assert result[0]["description"] == ""

    def test_list_presets_preset_with_description_only(self, tmp_path, monkeypatch):
        """preset key with only description but no name defaults to stem."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "desconly.yaml").write_text("preset:\n  description: A description\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert len(result) == 1
        assert result[0]["name"] == "desconly"
        assert result[0]["description"] == "A description"

    def test_list_presets_sorted_order(self, tmp_path, monkeypatch):
        """Presets should be returned in sorted filename order."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "zebra.yaml").write_text("preset:\n  name: zebra\n")
        (preset_dir / "alpha.yaml").write_text("preset:\n  name: alpha\n")
        (preset_dir / "middle.yaml").write_text("preset:\n  name: middle\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert len(result) == 3
        assert result[0]["name"] == "alpha"
        assert result[1]["name"] == "middle"
        assert result[2]["name"] == "zebra"

    def test_list_presets_ignores_non_yaml_files(self, tmp_path, monkeypatch):
        """Non-YAML files should be ignored."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "readme.txt").write_text("not a preset")
        (preset_dir / "data.json").write_text("{}")
        (preset_dir / "valid.yaml").write_text("preset:\n  name: valid\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_list_presets_empty_preset_dict(self, tmp_path, monkeypatch):
        """preset key that is an empty dict should use defaults."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "empty-meta.yaml").write_text("preset: {}\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert len(result) == 1
        assert result[0]["name"] == "empty-meta"
        assert result[0]["description"] == ""

    def test_list_presets_null_yaml(self, tmp_path, monkeypatch):
        """YAML file that parses to None should be skipped (not isinstance dict)."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "null.yaml").write_text("null\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert result == []

    def test_list_presets_integer_yaml(self, tmp_path, monkeypatch):
        """YAML file that parses to integer should be skipped."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "number.yaml").write_text("42\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = list_presets()
        assert result == []


class TestGetPresetEdgeCases:
    def test_get_preset_non_dict_yaml(self, tmp_path, monkeypatch):
        """get_preset() with YAML that parses to a list returns None."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "listfile.yaml").write_text("- a\n- b\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        assert get_preset("listfile") is None

    def test_get_preset_missing_preset_key(self, tmp_path, monkeypatch):
        """get_preset() with no 'preset' key uses name from argument."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "nopreskey.yaml").write_text("network_policies:\n  - allow: pypi\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = get_preset("nopreskey")
        assert result is not None
        assert result["name"] == "nopreskey"
        assert result["description"] == ""
        assert "network_policies" in result["policy"]

    def test_get_preset_returns_exact_structure(self, tmp_path, monkeypatch):
        """get_preset() returns exactly {name, description, policy}."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "full.yaml").write_text(
            "preset:\n  name: full-preset\n  description: Full desc\n"
            "network_policies:\n  - allow: all\n"
        )
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = get_preset("full")
        assert result is not None
        assert set(result.keys()) == {"name", "description", "policy"}
        assert result["name"] == "full-preset"
        assert result["description"] == "Full desc"
        # The "preset" key is popped, so policy should only have network_policies
        assert "preset" not in result["policy"]
        assert "network_policies" in result["policy"]

    def test_get_preset_pops_preset_key(self, tmp_path, monkeypatch):
        """get_preset() pops the 'preset' key from data so it's not in policy."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "poppable.yaml").write_text(
            "preset:\n  name: pop-test\n  description: Testing pop\nrules:\n  - deny: all\n"
        )
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        result = get_preset("poppable")
        assert result is not None
        assert "preset" not in result["policy"]
        assert "rules" in result["policy"]

    def test_get_preset_empty_string_name(self, tmp_path, monkeypatch):
        """get_preset with empty string is handled."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        # Empty name => ".yaml" file doesn't exist
        assert get_preset("") is None

    def test_get_preset_null_yaml(self, tmp_path, monkeypatch):
        """get_preset() with null YAML content returns None."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "nullfile.yaml").write_text("")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        assert get_preset("nullfile") is None

    def test_get_preset_traversal_with_dot_segments(self):
        """Various path traversal attempts return None."""
        assert get_preset("..") is None
        assert get_preset(".") is None
        assert get_preset("../..") is None

    def test_get_preset_oserror_returns_none(self, tmp_path, monkeypatch):
        """get_preset() returns None on OSError reading file."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        # Create a directory instead of a file — read_text() will fail
        (preset_dir / "badread.yaml").mkdir()
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)
        assert get_preset("badread") is None

    def test_list_presets_oserror_on_read(self, tmp_path, monkeypatch):
        """list_presets() handles OSError when reading a YAML file."""
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        # Can't make a directory with .yaml extension as a reliable OSError trigger
        # on all platforms, so use monkeypatching
        from unittest.mock import patch

        good_file = preset_dir / "good.yaml"
        good_file.write_text("preset:\n  name: good\n")
        monkeypatch.setattr("shoreguard.presets._PRESETS_DIR", preset_dir)

        original_read = good_file.read_text
        call_count = [0]

        def fail_read(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("permission denied")
            return original_read(*args, **kwargs)

        with patch.object(type(good_file), "read_text", fail_read):
            result = list_presets()
            # OSError is caught, file is skipped
            assert isinstance(result, list)
