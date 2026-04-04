"""Tests for notification message formatters."""

from __future__ import annotations

import json

from shoreguard.services.formatters import (
    _event_label,
    format_discord,
    format_email_body,
    format_generic,
    format_slack,
)

_SAMPLE_PAYLOAD = {"sandbox": "test-sb", "gateway": "dev", "actor": "admin@test.com"}
_SAMPLE_TIMESTAMP = "2026-04-04T12:00:00+00:00"


class TestEventLabel:
    def test_known_event(self):
        assert _event_label("approval.pending") == "Approval Pending"

    def test_unknown_event(self):
        assert _event_label("custom.event") == "Custom Event"


class TestFormatGeneric:
    def test_produces_valid_json(self):
        body = format_generic("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert data["event"] == "sandbox.created"
        assert data["timestamp"] == _SAMPLE_TIMESTAMP
        assert data["data"]["sandbox"] == "test-sb"


class TestFormatSlack:
    def test_produces_valid_json(self):
        body = format_slack("approval.pending", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "attachments" in data

    def test_has_blocks(self):
        body = format_slack("approval.approved", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "blocks" in data["attachments"][0]

    def test_contains_payload_fields(self):
        body = format_slack("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        assert "test-sb" in body
        assert "dev" in body


class TestFormatDiscord:
    def test_produces_valid_json(self):
        body = format_discord("sandbox.deleted", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "embeds" in data

    def test_has_fields(self):
        body = format_discord("approval.rejected", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "fields" in data["embeds"][0]

    def test_has_color(self):
        body = format_discord("approval.approved", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        data = json.loads(body)
        assert "color" in data["embeds"][0]


class TestFormatEmail:
    def test_produces_text(self):
        body = format_email_body("sandbox.created", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        assert isinstance(body, str)
        assert "Sandbox Created" in body

    def test_contains_fields(self):
        body = format_email_body("approval.pending", _SAMPLE_PAYLOAD, _SAMPLE_TIMESTAMP)
        assert "test-sb" in body
        assert "dev" in body
