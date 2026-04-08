"""Tests for OpenShell metadata loader (services/_openshell_meta.py)."""

from __future__ import annotations

from unittest.mock import patch

from shoreguard.services._openshell_meta import OpenShellMeta, get_openshell_meta

_SAMPLE_YAML = {
    "provider_types": {
        "nvidia": {"label": "NVIDIA NIM", "cred_key": "NVIDIA_API_KEY", "icon": "gpu-card"},
        "openai": {"label": "OpenAI", "cred_key": "OPENAI_API_KEY", "icon": "stars"},
    },
    "inference_providers": [
        {"name": "nvidia", "label": "NVIDIA NIM", "placeholder": "meta/llama-3.1-8b-instruct"},
    ],
    "community_sandboxes": [
        {"name": "base", "description": "Foundational image", "image": ""},
    ],
}


class TestOpenShellMeta:
    def test_provider_types_populated(self):
        meta = OpenShellMeta(_SAMPLE_YAML)
        assert "nvidia" in meta.provider_types
        assert meta.provider_types["nvidia"]["label"] == "NVIDIA NIM"
        assert meta.provider_types["nvidia"]["cred_key"] == "NVIDIA_API_KEY"

    def test_inference_providers_populated(self):
        meta = OpenShellMeta(_SAMPLE_YAML)
        assert len(meta.inference_providers) == 1
        assert meta.inference_providers[0]["name"] == "nvidia"
        assert meta.inference_providers[0]["label"] == "NVIDIA NIM"

    def test_community_sandboxes_populated(self):
        meta = OpenShellMeta(_SAMPLE_YAML)
        assert len(meta.community_sandboxes) == 1
        assert meta.community_sandboxes[0]["name"] == "base"
        assert meta.community_sandboxes[0]["description"] == "Foundational image"

    def test_missing_keys_default_to_empty(self):
        meta = OpenShellMeta({})
        assert meta.provider_types == {}
        assert meta.inference_providers == []
        assert meta.community_sandboxes == []

    def test_partial_keys(self):
        meta = OpenShellMeta({"provider_types": {"x": {"label": "X"}}})
        assert meta.provider_types == {"x": {"label": "X"}}
        assert meta.inference_providers == []
        assert meta.community_sandboxes == []

    def test_get_provider_type_found(self):
        meta = OpenShellMeta(_SAMPLE_YAML)
        result = meta.get_provider_type("nvidia")
        assert result is not None
        assert result["label"] == "NVIDIA NIM"
        assert result["cred_key"] == "NVIDIA_API_KEY"

    def test_get_provider_type_not_found(self):
        meta = OpenShellMeta(_SAMPLE_YAML)
        assert meta.get_provider_type("unknown") is None

    def test_get_provider_type_second_provider(self):
        meta = OpenShellMeta(_SAMPLE_YAML)
        result = meta.get_provider_type("openai")
        assert result is not None
        assert result["label"] == "OpenAI"


class TestGetOpenshellMeta:
    def test_returns_singleton(self):
        """get_openshell_meta returns same instance on repeated calls."""
        import shoreguard.services._openshell_meta as mod

        old_cached = mod._cached
        try:
            mod._cached = None
            first = get_openshell_meta()
            second = get_openshell_meta()
            assert first is second
        finally:
            mod._cached = old_cached

    def test_loads_real_yaml(self):
        """get_openshell_meta loads the bundled openshell.yaml successfully."""
        import shoreguard.services._openshell_meta as mod

        old_cached = mod._cached
        try:
            mod._cached = None
            meta = get_openshell_meta()
            assert isinstance(meta.provider_types, dict)
            assert len(meta.provider_types) > 0
            assert isinstance(meta.inference_providers, list)
            assert isinstance(meta.community_sandboxes, list)
        finally:
            mod._cached = old_cached

    def test_caches_after_first_load(self):
        """After first load, YAML file is not read again."""
        import shoreguard.services._openshell_meta as mod

        old_cached = mod._cached
        try:
            mod._cached = None
            first = get_openshell_meta()
            # Replace _YAML_PATH with a path that would fail if read
            with patch.object(mod, "_YAML_PATH", side_effect=RuntimeError):
                result = get_openshell_meta()
                assert result is first
        finally:
            mod._cached = old_cached
