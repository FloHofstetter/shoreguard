"""Focused tests for M29 sync milestone additions."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from shoreguard.api.routes.operations import _format_sse_event
from shoreguard.config import _always_blocked_networks, is_private_ip

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

    def teardown_method(self):
        _always_blocked_networks.cache_clear()

    def _with_blocked(self, value: str):
        class _Srv:
            always_blocked_ips = value

        class _Settings:
            server = _Srv()

        return patch("shoreguard.settings.get_settings", return_value=_Settings())

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
