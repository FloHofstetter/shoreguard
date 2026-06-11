"""Tests for one-tap approval links, Telegram/ntfy formatting, and the vote route."""

from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from httpx import ASGITransport, AsyncClient

from shoreguard.api.auth import core as auth_core
from shoreguard.services.approval_links import (
    enrich_approval_payload,
    make_one_tap_token,
    verify_one_tap_token,
)
from shoreguard.services.formatters import (
    format_ntfy,
    format_telegram,
    prepare_telegram_request,
)
from shoreguard.settings import reset_settings


@pytest.fixture(autouse=True)
def _hmac_secret() -> Generator[None]:
    prev = auth_core.state.hmac_secret
    auth_core.state.hmac_secret = b"test-secret-key-for-unit-tests!!"
    yield
    auth_core.state.hmac_secret = prev


@pytest.fixture
def _one_tap_enabled(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS", "true")
    monkeypatch.setenv("SHOREGUARD_PUBLIC_URL", "https://sg.tail1234.ts.net")
    reset_settings()
    yield
    monkeypatch.delenv("SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS", raising=False)
    monkeypatch.delenv("SHOREGUARD_PUBLIC_URL", raising=False)
    reset_settings()


def _token(decision: str = "approve", ttl: int = 600) -> str:
    return make_one_tap_token(
        gateway="gw1", sandbox="sb1", chunk_id="ch1", decision=decision, ttl=ttl
    )


# ─── token signing ─────────────────────────────────────────────────────────


def test_token_roundtrip() -> None:
    data = verify_one_tap_token(_token())
    assert data == {
        "gateway": "gw1",
        "sandbox": "sb1",
        "chunk_id": "ch1",
        "decision": "approve",
    }


def test_token_expiry() -> None:
    assert verify_one_tap_token(_token(ttl=-1)) is None


def test_token_tamper_rejected() -> None:
    token = _token()
    raw, _, sig = token.rpartition(".")
    assert verify_one_tap_token(f"{raw}x.{sig}") is None
    assert verify_one_tap_token(f"{raw}.{'0' * len(sig)}") is None
    assert verify_one_tap_token("garbage") is None


def test_token_invalid_decision_rejected() -> None:
    with pytest.raises(ValueError):
        make_one_tap_token(gateway="g", sandbox="s", chunk_id="c", decision="explode", ttl=60)


# ─── payload enrichment ────────────────────────────────────────────────────


def test_enrich_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS", raising=False)
    reset_settings()
    payload = {"gateway": "gw1", "sandbox": "sb1", "chunk_id": "ch1"}
    assert enrich_approval_payload("approval.pending", payload) == payload


def test_enrich_adds_links(_one_tap_enabled) -> None:
    payload = {"gateway": "gw1", "sandbox": "sb1", "chunk_id": "ch1"}
    out = enrich_approval_payload("approval.pending", payload)
    assert out["page_url"].startswith("https://sg.tail1234.ts.net/gateways/gw1/")
    assert "/approvals/one-tap?token=" in out["approve_url"]
    assert "/approvals/one-tap?token=" in out["reject_url"]
    # Original payload untouched
    assert "approve_url" not in payload


def test_enrich_without_chunk_only_page_url(_one_tap_enabled) -> None:
    out = enrich_approval_payload("approval.pending", {"gateway": "gw1", "sandbox": "sb1"})
    assert "page_url" in out
    assert "approve_url" not in out


def test_enrich_other_events_noop(_one_tap_enabled) -> None:
    payload = {"gateway": "gw1", "sandbox": "sb1", "chunk_id": "ch1"}
    assert enrich_approval_payload("sandbox.created", payload) == payload


# ─── formatters ────────────────────────────────────────────────────────────


def test_format_telegram_with_buttons() -> None:
    body = json.loads(
        format_telegram(
            "approval.pending",
            {"sandbox": "sb1", "approve_url": "https://x/a", "reject_url": "https://x/r"},
            "2026-06-11T00:00:00Z",
        )
    )
    assert body["parse_mode"] == "HTML"
    buttons = body["reply_markup"]["inline_keyboard"][0]
    assert [b["url"] for b in buttons] == ["https://x/a", "https://x/r"]


def test_prepare_telegram_request_moves_chat_id() -> None:
    url = "https://api.telegram.org/botTOKEN/sendMessage?chat_id=4242"
    post_url, body = prepare_telegram_request(url, json.dumps({"chat_id": "", "text": "hi"}))
    assert post_url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert json.loads(body)["chat_id"] == "4242"


def test_format_ntfy_actions() -> None:
    body = json.loads(
        format_ntfy(
            "approval.pending",
            {
                "sandbox": "sb1",
                "approve_url": "https://x/a",
                "reject_url": "https://x/r",
                "page_url": "https://x/p",
            },
            "2026-06-11T00:00:00Z",
        )
    )
    assert body["click"] == "https://x/p"
    assert [a["url"] for a in body["actions"]] == ["https://x/a", "https://x/r"]
    assert all(a["action"] == "view" for a in body["actions"])


# ─── route ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    from tests.conftest import make_auth_test_db

    factory, dispose = make_auth_test_db()
    yield factory
    dispose()


def _client() -> AsyncClient:
    from shoreguard.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_route_404_when_disabled(db, monkeypatch) -> None:
    monkeypatch.delenv("SHOREGUARD_WEBHOOK_ONE_TAP_APPROVALS", raising=False)
    reset_settings()
    async with _client() as client:
        resp = await client.post("/api/approvals/one-tap", json={"token": _token()})
        assert resp.status_code == 404


async def test_route_400_on_bad_token(db, _one_tap_enabled) -> None:
    async with _client() as client:
        resp = await client.post("/api/approvals/one-tap", json={"token": "nonsense"})
        assert resp.status_code == 400


async def test_route_casts_vote(db, _one_tap_enabled, monkeypatch) -> None:
    from shoreguard.api.routes import one_tap as one_tap_module
    from shoreguard.container import get_container

    calls: list[tuple[str, str]] = []

    class _FakeApprovalService:
        def __init__(self, client) -> None:
            pass

        async def approve(self, sandbox: str, chunk_id: str) -> dict:
            calls.append((sandbox, chunk_id))
            return {"status": "approved"}

    async def _fake_get_client(name: str):
        return object()

    monkeypatch.setattr(one_tap_module, "ApprovalService", _FakeApprovalService)
    monkeypatch.setattr(get_container().gateway, "get_client", _fake_get_client)

    async with _client() as client:
        resp = await client.post("/api/approvals/one-tap", json={"token": _token()})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"
        assert calls == [("sb1", "ch1")]


async def test_page_renders_for_valid_token(db, _one_tap_enabled) -> None:
    async with _client() as client:
        resp = await client.get("/approvals/one-tap", params={"token": _token()})
        assert resp.status_code == 200
        assert "one-tap-approval" in resp.text


async def test_page_rejects_invalid_token(db, _one_tap_enabled) -> None:
    async with _client() as client:
        resp = await client.get("/approvals/one-tap", params={"token": "junk"})
        assert resp.status_code == 400
