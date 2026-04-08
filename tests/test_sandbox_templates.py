"""Unit tests for sandbox template loading (pure functions, no gRPC)."""

from __future__ import annotations

import pathlib
from unittest.mock import patch

from shoreguard.sandbox_templates import get_template, list_templates


class TestListTemplates:
    def test_returns_list(self):
        result = list_templates()
        assert isinstance(result, list)

    def test_returns_at_least_one_entry(self):
        result = list_templates()
        assert len(result) >= 1

    def test_each_entry_has_required_keys(self):
        for entry in list_templates():
            assert "name" in entry
            assert "description" in entry
            assert "category" in entry
            assert "file" in entry

    def test_file_ends_with_yaml(self):
        for entry in list_templates():
            assert entry["file"].endswith(".yaml")

    def test_contains_known_templates(self):
        names = [t["name"] for t in list_templates()]
        assert "web-dev" in names

    def test_name_is_string(self):
        for entry in list_templates():
            assert isinstance(entry["name"], str)
            assert len(entry["name"]) > 0

    def test_description_is_string(self):
        for entry in list_templates():
            assert isinstance(entry["description"], str)

    def test_category_is_string(self):
        for entry in list_templates():
            assert isinstance(entry["category"], str)

    def test_returns_empty_when_dir_missing(self):
        with patch(
            "shoreguard.sandbox_templates._TEMPLATES_DIR", pathlib.Path("/nonexistent/path")
        ):
            result = list_templates()
            assert result == []

    def test_sorted_output(self):
        result = list_templates()
        files = [t["file"] for t in result]
        assert files == sorted(files)

    def test_skips_invalid_yaml(self, tmp_path):
        bad_file = tmp_path / "broken.yaml"
        bad_file.write_text("{{invalid yaml: [")
        good_file = tmp_path / "good.yaml"
        good_file.write_text(
            "template:\n  name: good\n  description: A good template\n  category: test\n"
        )
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = list_templates()
            names = [t["name"] for t in result]
            assert "good" in names

    def test_skips_non_dict_yaml(self, tmp_path):
        bad_file = tmp_path / "list.yaml"
        bad_file.write_text("- item1\n- item2\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = list_templates()
            assert len(result) == 0

    def test_defaults_name_to_stem(self, tmp_path):
        f = tmp_path / "mytemplate.yaml"
        f.write_text("template:\n  description: desc\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = list_templates()
            assert result[0]["name"] == "mytemplate"

    def test_defaults_description_to_empty(self, tmp_path):
        f = tmp_path / "t.yaml"
        f.write_text("template:\n  name: t\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = list_templates()
            assert result[0]["description"] == ""

    def test_defaults_category_to_empty(self, tmp_path):
        f = tmp_path / "t.yaml"
        f.write_text("template:\n  name: t\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = list_templates()
            assert result[0]["category"] == ""

    def test_missing_template_key_uses_defaults(self, tmp_path):
        f = tmp_path / "bare.yaml"
        f.write_text("other_key: value\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = list_templates()
            assert len(result) == 1
            assert result[0]["name"] == "bare"
            assert result[0]["description"] == ""
            assert result[0]["category"] == ""


class TestGetTemplate:
    def test_existing_template(self):
        result = get_template("web-dev")
        assert result is not None
        assert result["name"] == "web-dev"
        assert "description" in result
        assert "category" in result
        assert "sandbox" in result
        assert isinstance(result["sandbox"], dict)

    def test_nonexistent_returns_none(self):
        assert get_template("nonexistent-template-xyz") is None

    def test_path_traversal_rejected(self):
        assert get_template("../../etc/passwd") is None
        assert get_template("../../../etc/shadow") is None

    def test_all_bundled_templates_loadable(self):
        for entry in list_templates():
            name = entry["file"].replace(".yaml", "")
            result = get_template(name)
            assert result is not None, f"Failed to load template: {name}"
            assert "name" in result
            assert "description" in result
            assert "category" in result
            assert "sandbox" in result

    def test_result_has_sandbox_dict(self):
        result = get_template("web-dev")
        assert isinstance(result["sandbox"], dict)

    def test_returns_none_for_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{invalid")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            assert get_template("bad") is None

    def test_returns_none_for_non_dict_yaml(self, tmp_path):
        f = tmp_path / "listfile.yaml"
        f.write_text("- a\n- b\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            assert get_template("listfile") is None

    def test_defaults_name_to_template_name_arg(self, tmp_path):
        f = tmp_path / "myname.yaml"
        f.write_text("template:\n  description: foo\nsandbox:\n  image: img\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = get_template("myname")
            assert result["name"] == "myname"

    def test_defaults_description_to_empty(self, tmp_path):
        f = tmp_path / "t.yaml"
        f.write_text("template:\n  name: t\nsandbox: {}\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = get_template("t")
            assert result["description"] == ""

    def test_defaults_category_to_empty(self, tmp_path):
        f = tmp_path / "t.yaml"
        f.write_text("template:\n  name: t\nsandbox: {}\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = get_template("t")
            assert result["category"] == ""

    def test_missing_sandbox_key_defaults_to_empty_dict(self, tmp_path):
        f = tmp_path / "nosb.yaml"
        f.write_text("template:\n  name: nosb\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = get_template("nosb")
            assert result["sandbox"] == {}

    def test_missing_template_key_uses_defaults(self, tmp_path):
        f = tmp_path / "bare.yaml"
        f.write_text("sandbox:\n  image: test\n")
        with patch("shoreguard.sandbox_templates._TEMPLATES_DIR", tmp_path):
            result = get_template("bare")
            assert result["name"] == "bare"
            assert result["description"] == ""
            assert result["category"] == ""
            assert result["sandbox"] == {"image": "test"}
