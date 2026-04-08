"""Tests for notification message formatters."""

from __future__ import annotations

import json

from shoreguard.services.formatters import (
    FORMATTERS,
    _event_label,
    _payload_fields,
    format_discord,
    format_email_body,
    format_generic,
    format_slack,
)

_SAMPLE_PAYLOAD = {"sandbox": "test-sb", "gateway": "dev", "actor": "admin@test.com"}
_SAMPLE_TIMESTAMP = "2026-04-04T12:00:00+00:00"

# Payloads exercising every recognized key in _payload_fields
_FULL_PAYLOAD = {
    "sandbox": "sb-1",
    "gateway": "gw-1",
    "actor": "user@example.com",
    "reason": "testing",
    "message": "hello",
    "provider": "nvidia",
    "model": "llama-3",
    "image": "ghcr.io/img:latest",
    "endpoint": "https://api.example.com",
}


# ── _event_label ─────────────────────────────────────────────────────────────


class TestEventLabel:
    def test_known_event(self):
        assert _event_label("approval.pending") == "Approval Pending"

    def test_all_known_events(self):
        known = {
            "approval.pending": "Approval Pending",
            "approval.approved": "Approval Approved",
            "approval.rejected": "Approval Rejected",
            "sandbox.created": "Sandbox Created",
            "sandbox.deleted": "Sandbox Deleted",
            "gateway.registered": "Gateway Registered",
            "gateway.unregistered": "Gateway Unregistered",
            "inference.updated": "Inference Updated",
            "policy.updated": "Policy Updated",
            "webhook.test": "Test Event",
        }
        for event, label in known.items():
            assert _event_label(event) == label

    def test_unknown_event_fallback(self):
        result = _event_label("custom.event")
        assert result == "Custom Event"

    def test_unknown_event_replaces_dot_and_titles(self):
        result = _event_label("foo.bar.baz")
        assert result == "Foo Bar Baz"


# ── _payload_fields ──────────────────────────────────────────────────────────


class TestPayloadFields:
    def test_extracts_known_keys(self):
        fields = _payload_fields(_SAMPLE_PAYLOAD)
        labels = [f[0] for f in fields]
        values = [f[1] for f in fields]
        assert "Sandbox" in labels
        assert "Gateway" in labels
        assert "Actor" in labels
        assert "test-sb" in values
        assert "dev" in values
        assert "admin@test.com" in values

    def test_all_recognized_keys(self):
        fields = _payload_fields(_FULL_PAYLOAD)
        assert len(fields) == 9
        labels = [f[0] for f in fields]
        assert labels == [
            "Sandbox",
            "Gateway",
            "Actor",
            "Reason",
            "Message",
            "Provider",
            "Model",
            "Image",
            "Endpoint",
        ]
        values = [f[1] for f in fields]
        assert values == [
            "sb-1",
            "gw-1",
            "user@example.com",
            "testing",
            "hello",
            "nvidia",
            "llama-3",
            "ghcr.io/img:latest",
            "https://api.example.com",
        ]

    def test_preserves_order(self):
        """Keys appear in the defined iteration order, not insertion order."""
        fields = _payload_fields({"endpoint": "e", "sandbox": "s"})
        labels = [f[0] for f in fields]
        assert labels == ["Sandbox", "Endpoint"]

    def test_empty_payload(self):
        assert _payload_fields({}) == []

    def test_ignores_unknown_keys(self):
        fields = _payload_fields({"unknown_key": "value", "sandbox": "sb"})
        assert len(fields) == 1
        assert fields[0] == ("Sandbox", "sb")

    def test_converts_non_string_values(self):
        fields = _payload_fields({"sandbox": 42})
        assert fields[0] == ("Sandbox", "42")

    def test_each_key_individually(self):
        for key in (
            "sandbox",
            "gateway",
            "actor",
            "reason",
            "message",
            "provider",
            "model",
            "image",
            "endpoint",
        ):
            fields = _payload_fields({key: f"val-{key}"})
            assert len(fields) == 1
            assert fields[0] == (key.title(), f"val-{key}")


# ── format_generic ───────────────────────────────────────────────────────────


class TestFormatGeneric:
    def test_produces_valid_json(self):
        body = format_generic("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["event"] == "sandbox.created"
        assert data["timestamp"] == _SAMPLE_TIMESTAMP
        assert data["data"]["sandbox"] == "test-sb"

    def test_contains_all_payload_data(self):
        body = format_generic("sandbox.created", _FULL_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["data"] == _FULL_PAYLOAD

    def test_default_serializer_handles_non_json_types(self):
        from datetime import datetime

        payload = {"sandbox": "sb", "ts": datetime(2026, 1, 1)}
        body = format_generic("test", payload, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "2026" in data["data"]["ts"]


# ── format_slack ─────────────────────────────────────────────────────────────


class TestFormatSlack:
    def test_structure(self):
        body = format_slack("approval.pending", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "attachments" in data
        att = data["attachments"][0]
        assert "color" in att
        assert "blocks" in att
        assert len(att["blocks"]) == 2

    def test_color_for_known_events(self):
        colors = {
            "approval.pending": "warning",
            "approval.approved": "good",
            "approval.rejected": "danger",
            "sandbox.created": "#2196F3",
            "sandbox.deleted": "#9E9E9E",
            "gateway.registered": "good",
            "gateway.unregistered": "warning",
            "webhook.test": "#6C757D",
        }
        for event, expected_color in colors.items():
            body = format_slack(event, {}, _SAMPLE_TIMESTAMP)
            data = json.loads(body)
            assert data["attachments"][0]["color"] == expected_color, f"Wrong color for {event}"

    def test_unknown_event_default_color(self):
        body = format_slack("unknown.event", {}, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["attachments"][0]["color"] == "#6C757D"

    def test_section_block_contains_label(self):
        body = format_slack("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        section = data["attachments"][0]["blocks"][0]
        assert section["type"] == "section"
        assert "Sandbox Created" in section["text"]["text"]
        assert section["text"]["type"] == "mrkdwn"

    def test_section_block_contains_fields(self):
        body = format_slack("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        text = data["attachments"][0]["blocks"][0]["text"]["text"]
        assert "*Sandbox:*" in text or "Sandbox:" in text
        assert "test-sb" in text
        assert "*Gateway:*" in text or "Gateway:" in text
        assert "dev" in text

    def test_context_block_contains_timestamp(self):
        body = format_slack("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        context = data["attachments"][0]["blocks"][1]
        assert context["type"] == "context"
        assert len(context["elements"]) == 1
        assert context["elements"][0]["type"] == "mrkdwn"
        assert "ShoreGuard" in context["elements"][0]["text"]
        assert _SAMPLE_TIMESTAMP in context["elements"][0]["text"]

    def test_empty_payload_no_field_text(self):
        body = format_slack("webhook.test", {}, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        text = data["attachments"][0]["blocks"][0]["text"]["text"]
        assert "Test Event" in text

    def test_all_payload_fields_in_text(self):
        body = format_slack("sandbox.created", _FULL_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        text = data["attachments"][0]["blocks"][0]["text"]["text"]
        for key, val in _FULL_PAYLOAD.items():
            assert str(val) in text


# ── format_discord ───────────────────────────────────────────────────────────


class TestFormatDiscord:
    def test_structure(self):
        body = format_discord("sandbox.deleted", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "embeds" in data
        embed = data["embeds"][0]
        assert "title" in embed
        assert "color" in embed
        assert "timestamp" in embed
        assert "footer" in embed

    def test_title_is_event_label(self):
        body = format_discord("sandbox.created", {}, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["embeds"][0]["title"] == "Sandbox Created"

    def test_color_for_known_events(self):
        colors = {
            "approval.pending": 0xFFA500,
            "approval.approved": 0x2ECC71,
            "approval.rejected": 0xE74C3C,
            "sandbox.created": 0x2196F3,
            "sandbox.deleted": 0x9E9E9E,
            "gateway.registered": 0x2ECC71,
            "gateway.unregistered": 0xFFA500,
            "webhook.test": 0x6C757D,
        }
        for event, expected_color in colors.items():
            body = format_discord(event, {}, _SAMPLE_TIMESTAMP)
            data = json.loads(body)
            assert data["embeds"][0]["color"] == expected_color, f"Wrong color for {event}"

    def test_unknown_event_default_color(self):
        body = format_discord("unknown.event", {}, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["embeds"][0]["color"] == 0x6C757D

    def test_timestamp_in_embed(self):
        body = format_discord("sandbox.created", {}, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["embeds"][0]["timestamp"] == _SAMPLE_TIMESTAMP

    def test_footer_text(self):
        body = format_discord("sandbox.created", {}, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["embeds"][0]["footer"]["text"] == "ShoreGuard"

    def test_fields_present_with_payload(self):
        body = format_discord("approval.rejected", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        embed = data["embeds"][0]
        assert "fields" in embed
        field_names = [f["name"] for f in embed["fields"]]
        assert "Sandbox" in field_names
        assert "Gateway" in field_names
        assert "Actor" in field_names

    def test_fields_have_inline_true(self):
        body = format_discord("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        for field in data["embeds"][0]["fields"]:
            assert field["inline"] is True

    def test_fields_values_match(self):
        body = format_discord("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        fields = {f["name"]: f["value"] for f in data["embeds"][0]["fields"]}
        assert fields["Sandbox"] == "test-sb"
        assert fields["Gateway"] == "dev"
        assert fields["Actor"] == "admin@test.com"

    def test_no_fields_key_with_empty_payload(self):
        body = format_discord("webhook.test", {}, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "fields" not in data["embeds"][0]

    def test_all_payload_fields(self):
        body = format_discord("sandbox.created", _FULL_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        fields = data["embeds"][0]["fields"]
        assert len(fields) == 9


# ── format_email_body ────────────────────────────────────────────────────────


class TestFormatEmail:
    def test_header_line(self):
        body = format_email_body("sandbox.created", {}, _SAMPLE_TIMESTAMP)
        lines = body.split("\n")
        assert lines[0] == "ShoreGuard — Sandbox Created"

    def test_timestamp_line(self):
        body = format_email_body("sandbox.created", {}, _SAMPLE_TIMESTAMP)
        lines = body.split("\n")
        assert lines[1] == f"Time: {_SAMPLE_TIMESTAMP}"

    def test_blank_line_after_header(self):
        body = format_email_body("sandbox.created", {}, _SAMPLE_TIMESTAMP)
        lines = body.split("\n")
        assert lines[2] == ""

    def test_fields_in_body(self):
        body = format_email_body("approval.pending", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        assert "Sandbox: test-sb" in body
        assert "Gateway: dev" in body
        assert "Actor: admin@test.com" in body

    def test_all_fields(self):
        body = format_email_body("sandbox.created", _FULL_PAYLOAD, _SAMPLE_TIMESTAMP)
        for key, val in _FULL_PAYLOAD.items():
            assert f"{key.title()}: {val}" in body

    def test_returns_string(self):
        body = format_email_body("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        assert isinstance(body, str)


# ── FORMATTERS dict ──────────────────────────────────────────────────────────


class TestFormattersDict:
    def test_all_keys_present(self):
        assert set(FORMATTERS.keys()) == {"generic", "slack", "discord", "email"}

    def test_generic_is_format_generic(self):
        assert FORMATTERS["generic"] is format_generic

    def test_slack_is_format_slack(self):
        assert FORMATTERS["slack"] is format_slack

    def test_discord_is_format_discord(self):
        assert FORMATTERS["discord"] is format_discord

    def test_email_is_format_email_body(self):
        assert FORMATTERS["email"] is format_email_body
