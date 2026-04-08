"""Tests for Pydantic request model field validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestCreateProviderRequest:
    def _cls(self):
        from shoreguard.api.routes.providers import CreateProviderRequest

        return CreateProviderRequest

    def test_valid(self):
        req = self._cls()(name="openai-prod", type="openai", api_key="sk-123")
        assert req.name == "openai-prod"

    def test_name_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(name="x" * 300, type="openai")

    def test_name_empty(self):
        with pytest.raises(ValidationError):
            self._cls()(name="", type="openai")

    def test_name_invalid_chars(self):
        with pytest.raises(ValidationError):
            self._cls()(name="has spaces!", type="openai")

    def test_name_starts_with_dash(self):
        with pytest.raises(ValidationError):
            self._cls()(name="-invalid", type="openai")

    def test_type_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(name="ok", type="x" * 101)

    def test_type_empty(self):
        with pytest.raises(ValidationError):
            self._cls()(name="ok", type="")

    def test_api_key_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(name="ok", type="openai", api_key="k" * 513)

    def test_credentials_too_many(self):
        creds = {f"k{i}": "v" for i in range(51)}
        with pytest.raises(ValidationError, match="too many"):
            self._cls()(name="ok", type="openai", credentials=creds)

    def test_credentials_value_too_long(self):
        with pytest.raises(ValidationError, match="8192"):
            self._cls()(name="ok", type="openai", credentials={"k": "v" * 8193})

    def test_config_key_too_long(self):
        with pytest.raises(ValidationError, match="256"):
            self._cls()(name="ok", type="openai", config={"k" * 257: "v"})


class TestUpdateProviderRequest:
    def _cls(self):
        from shoreguard.api.routes.providers import UpdateProviderRequest

        return UpdateProviderRequest

    def test_valid_empty(self):
        req = self._cls()()
        assert req.type == ""

    def test_type_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(type="x" * 101)

    def test_credentials_too_many(self):
        creds = {f"k{i}": "v" for i in range(51)}
        with pytest.raises(ValidationError, match="too many"):
            self._cls()(credentials=creds)


class TestWebhookCreateRequest:
    def _cls(self):
        from shoreguard.api.routes.webhooks import WebhookCreateRequest

        return WebhookCreateRequest

    def test_valid(self):
        req = self._cls()(url="https://example.com/hook", event_types=["sandbox.created"])
        assert req.url == "https://example.com/hook"

    def test_url_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(url="https://x.com/" + "a" * 2048, event_types=["e"])

    def test_event_types_too_many(self):
        with pytest.raises(ValidationError):
            self._cls()(url="https://x.com", event_types=[f"e{i}" for i in range(51)])

    def test_event_type_empty_string(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._cls()(url="https://x.com", event_types=[""])

    def test_event_type_too_long(self):
        with pytest.raises(ValidationError, match="100"):
            self._cls()(url="https://x.com", event_types=["e" * 101])

    def test_invalid_channel_type(self):
        with pytest.raises(ValidationError, match="must be one of"):
            self._cls()(url="https://x.com", event_types=["e"], channel_type="invalid")

    def test_valid_channel_types(self):
        for ct in ("generic", "slack", "discord", "email"):
            req = self._cls()(url="https://x.com", event_types=["e"], channel_type=ct)
            assert req.channel_type == ct


class TestCreateSandboxRequest:
    def _cls(self):
        from shoreguard.api.routes.sandboxes import CreateSandboxRequest

        return CreateSandboxRequest

    def test_valid_defaults(self):
        req = self._cls()()
        assert req.name == ""
        assert req.environment == {}

    def test_name_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(name="x" * 254)

    def test_image_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(image="x" * 513)

    def test_providers_too_many(self):
        with pytest.raises(ValidationError):
            self._cls()(providers=[f"p{i}" for i in range(21)])

    def test_env_too_many(self):
        env = {f"K{i}": "v" for i in range(101)}
        with pytest.raises(ValidationError, match="too many"):
            self._cls()(environment=env)

    def test_env_value_too_long(self):
        with pytest.raises(ValidationError, match="8192"):
            self._cls()(environment={"K": "v" * 8193})

    def test_presets_too_many(self):
        with pytest.raises(ValidationError):
            self._cls()(presets=[f"p{i}" for i in range(21)])


class TestExecRequest:
    def _cls(self):
        from shoreguard.api.routes.sandboxes import ExecRequest

        return ExecRequest

    def test_valid(self):
        req = self._cls()(command="ls -la")
        assert req.timeout_seconds == 0

    def test_timeout_negative(self):
        with pytest.raises(ValidationError):
            self._cls()(command="ls", timeout_seconds=-1)

    def test_timeout_too_high(self):
        with pytest.raises(ValidationError):
            self._cls()(command="ls", timeout_seconds=3601)

    def test_workdir_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(command="ls", workdir="/" * 4097)

    def test_env_too_many(self):
        env = {f"K{i}": "v" for i in range(101)}
        with pytest.raises(ValidationError, match="too many"):
            self._cls()(command="ls", env=env)


class TestSetInferenceRequest:
    def _cls(self):
        from shoreguard.api.main import SetInferenceRequest

        return SetInferenceRequest

    def test_valid(self):
        req = self._cls()(provider_name="openai", model_id="gpt-4")
        assert req.verify is True

    def test_provider_name_empty(self):
        with pytest.raises(ValidationError):
            self._cls()(provider_name="", model_id="gpt-4")

    def test_model_id_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(provider_name="openai", model_id="m" * 254)

    def test_timeout_negative(self):
        with pytest.raises(ValidationError):
            self._cls()(provider_name="openai", model_id="gpt-4", timeout_secs=-1)

    def test_timeout_too_high(self):
        with pytest.raises(ValidationError):
            self._cls()(provider_name="openai", model_id="gpt-4", timeout_secs=3601)


class TestLoginRequest:
    def _cls(self):
        from shoreguard.api.pages import LoginRequest

        return LoginRequest

    def test_valid(self):
        req = self._cls()(email="test@example.com", password="secret123")
        assert req.email == "test@example.com"

    def test_email_empty(self):
        with pytest.raises(ValidationError):
            self._cls()(email="", password="secret")

    def test_email_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(email="x" * 255, password="secret")

    def test_password_empty(self):
        with pytest.raises(ValidationError):
            self._cls()(email="test@example.com", password="")

    def test_password_too_long(self):
        with pytest.raises(ValidationError):
            self._cls()(email="test@example.com", password="p" * 129)


class TestPolicyModels:
    def test_network_rule_key_empty(self):
        from shoreguard.api.routes.policies import NetworkRuleRequest

        with pytest.raises(ValidationError):
            NetworkRuleRequest(key="", rule={"allow": True})

    def test_network_rule_key_too_long(self):
        from shoreguard.api.routes.policies import NetworkRuleRequest

        with pytest.raises(ValidationError):
            NetworkRuleRequest(key="k" * 254, rule={"allow": True})

    def test_filesystem_path_too_long(self):
        from shoreguard.api.routes.policies import FilesystemPathRequest

        with pytest.raises(ValidationError):
            FilesystemPathRequest(path="/" * 4097, access="ro")

    def test_filesystem_path_empty(self):
        from shoreguard.api.routes.policies import FilesystemPathRequest

        with pytest.raises(ValidationError):
            FilesystemPathRequest(path="", access="ro")

    def test_process_policy_user_too_long(self):
        from shoreguard.api.routes.policies import ProcessPolicyRequest

        with pytest.raises(ValidationError):
            ProcessPolicyRequest(run_as_user="u" * 254)

    def test_landlock_too_long(self):
        from shoreguard.api.routes.policies import ProcessPolicyRequest

        with pytest.raises(ValidationError):
            ProcessPolicyRequest(landlock_compatibility="x" * 51)


class TestRejectRequest:
    def test_reason_too_long(self):
        from shoreguard.api.routes.approvals import RejectRequest

        with pytest.raises(ValidationError):
            RejectRequest(reason="r" * 1001)

    def test_reason_default_empty(self):
        from shoreguard.api.routes.approvals import RejectRequest

        req = RejectRequest()
        assert req.reason == ""
