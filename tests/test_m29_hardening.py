"""Focused tests for M29 sync milestone additions."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from shoreguard.api.routes.operations import _format_sse_event
from shoreguard.config import _always_blocked_networks, _ssrf_allowed_networks, is_private_ip


def _with_ip_lists(blocked: str = "", allowed: str = ""):
    class _Srv:
        always_blocked_ips = blocked
        ssrf_allowed_ips = allowed

    class _Settings:
        server = _Srv()

    return patch("shoreguard.settings.get_settings", return_value=_Settings())


# ---------------------------------------------------------------------------
# SSE formatter (upstream #842)
# ---------------------------------------------------------------------------


class TestSseFormatter:
    def test_basic_event_shape(self):
        out = _format_sse_event("status", {"state": "running"})
        assert out.startswith("event: status\n")
        assert out.endswith("\n\n")
        payload_line = out.split("\n")[1]
        assert payload_line.startswith("data: ")
        assert json.loads(payload_line[len("data: ") :]) == {"state": "running"}

    def test_no_event_name_omits_event_line(self):
        out = _format_sse_event(None, {"x": 1})
        assert not out.startswith("event:")
        assert out.startswith("data: ")

    def test_newline_in_string_cannot_break_framing(self):
        payload = {"msg": "line1\nline2\r\nline3"}
        out = _format_sse_event("error", payload)
        body = out[:-2]  # strip terminator
        assert "\n" not in body.split("data: ", 1)[1]
        assert "\r" not in body

    def test_null_byte_stripped(self):
        out = _format_sse_event("error", {"msg": "bad\x00data"})
        assert "\x00" not in out


# ---------------------------------------------------------------------------
# Always-blocked IPs (upstream #814)
# ---------------------------------------------------------------------------


class TestAlwaysBlockedIps:
    def setup_method(self):
        _always_blocked_networks.cache_clear()
        _ssrf_allowed_networks.cache_clear()

    def teardown_method(self):
        _always_blocked_networks.cache_clear()
        _ssrf_allowed_networks.cache_clear()

    def _with_blocked(self, value: str):
        return _with_ip_lists(blocked=value)

    def test_empty_list_is_noop(self):
        with self._with_blocked(""):
            assert _always_blocked_networks() == ()

    def test_cidr_match_blocks_public_ip(self):
        with self._with_blocked("8.8.8.0/24"):
            assert is_private_ip("8.8.8.42") is True

    def test_public_ip_outside_list_not_blocked(self):
        with self._with_blocked("8.8.8.0/24"):
            assert is_private_ip("1.1.1.1") is False

    def test_exact_ip_in_list(self):
        with self._with_blocked("8.8.4.4"):
            assert is_private_ip("8.8.4.4") is True

    def test_settings_load_rejects_bad_cidr(self):
        from pydantic import ValidationError as PydanticValidationError

        from shoreguard.settings import ServerSettings

        with pytest.raises(PydanticValidationError, match="invalid CIDR"):
            ServerSettings(always_blocked_ips="not-a-cidr")


# ---------------------------------------------------------------------------
# SSRF allowlist (#13)
# ---------------------------------------------------------------------------


class TestSsrfAllowedIps:
    def setup_method(self):
        _always_blocked_networks.cache_clear()
        _ssrf_allowed_networks.cache_clear()

    def teardown_method(self):
        _always_blocked_networks.cache_clear()
        _ssrf_allowed_networks.cache_clear()

    def test_exact_ip_exempted(self):
        with _with_ip_lists(allowed="192.168.1.10"):
            assert is_private_ip("192.168.1.10") is False

    def test_cidr_member_exempted(self):
        with _with_ip_lists(allowed="192.168.1.0/24"):
            assert is_private_ip("192.168.1.42") is False

    def test_private_ip_outside_allowlist_still_blocked(self):
        with _with_ip_lists(allowed="192.168.1.0/24"):
            assert is_private_ip("192.168.2.1") is True
            assert is_private_ip("10.0.0.5") is True

    def test_always_blocked_beats_allowlist(self):
        with _with_ip_lists(blocked="192.168.1.10", allowed="192.168.1.0/24"):
            assert is_private_ip("192.168.1.10") is True
            assert is_private_ip("192.168.1.11") is False

    def test_public_ip_unaffected(self):
        with _with_ip_lists(allowed="192.168.1.0/24"):
            assert is_private_ip("8.8.8.8") is False

    def test_localhost_hostname_stays_private(self):
        """The literal 'localhost' short-circuits before the allowlist."""
        with _with_ip_lists(allowed="127.0.0.1"):
            assert is_private_ip("localhost") is True
            assert is_private_ip("127.0.0.1") is False

    def test_hostname_resolving_to_allowlisted_private_ip(self):
        fake_result = [(2, 1, 6, "", ("192.168.1.10", 0))]
        with (
            _with_ip_lists(allowed="192.168.1.0/24"),
            patch("shoreguard.config.socket.getaddrinfo", return_value=fake_result),
        ):
            assert is_private_ip("auth.homelab.lan") is False

    def test_settings_load_rejects_bad_cidr(self):
        from pydantic import ValidationError as PydanticValidationError

        from shoreguard.settings import ServerSettings

        with pytest.raises(PydanticValidationError, match="SHOREGUARD_SSRF_ALLOWED_IPS"):
            ServerSettings(ssrf_allowed_ips="not-a-cidr")

    def test_webhook_url_validation_end_to_end(self, monkeypatch):
        """validate_webhook_url honours the allowlist via real settings (no mocks)."""
        from shoreguard.api.validation import validate_webhook_url
        from shoreguard.exceptions import ValidationError as DomainValidationError
        from shoreguard.settings import reset_settings

        monkeypatch.delenv("SHOREGUARD_LOCAL_MODE", raising=False)
        reset_settings()
        with pytest.raises(DomainValidationError, match="SHOREGUARD_SSRF_ALLOWED_IPS"):
            validate_webhook_url("https://192.168.1.10/hook")

        monkeypatch.setenv("SHOREGUARD_SSRF_ALLOWED_IPS", "192.168.1.0/24")
        reset_settings()
        _ssrf_allowed_networks.cache_clear()
        assert validate_webhook_url("https://192.168.1.10/hook") == "https://192.168.1.10/hook"

    def test_prod_readiness_warns_on_slash_zero(self, monkeypatch):
        from shoreguard.settings import get_settings, reset_settings

        monkeypatch.setenv("SHOREGUARD_SSRF_ALLOWED_IPS", "0.0.0.0/0")
        reset_settings()
        warnings = get_settings().check_production_readiness()
        assert any("ssrf_allowed_ips" in w and "/0" in w for w in warnings)

    def test_prod_readiness_warns_on_local_mode_redundancy(self, monkeypatch):
        from shoreguard.settings import get_settings, reset_settings

        monkeypatch.setenv("SHOREGUARD_SSRF_ALLOWED_IPS", "192.168.1.0/24")
        monkeypatch.setenv("SHOREGUARD_LOCAL_MODE", "true")
        reset_settings()
        warnings = get_settings().check_production_readiness()
        assert any("ssrf_allowed_ips is set while local_mode" in w for w in warnings)
