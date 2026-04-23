"""Unit tests for the proactive cert-rotation service (WS35.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from shoreguard.services.cert_rotation import CertRotationService


def _cert_info(seconds_until_expiry: float | None) -> SimpleNamespace:
    """Build a minimal object that mimics ``_tls.CertInfo``."""
    return SimpleNamespace(
        seconds_until_expiry=seconds_until_expiry,
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds_until_expiry or 0),
    )


def _client(cert_info: SimpleNamespace | None) -> MagicMock:
    client = MagicMock()
    client.cert_info = cert_info
    client.reload_credentials = MagicMock()
    return client


def _gateway_service(
    gateways: list[dict[str, str]],
    *,
    creds: dict[str, dict[str, bytes]] | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.list_all = MagicMock(return_value=gateways)
    # Back-door registry access mirrors what cert_rotation uses.
    registry = MagicMock()
    registry.get_credentials = MagicMock(side_effect=lambda name: (creds or {}).get(name))
    svc._registry = registry  # noqa: SLF001
    return svc


def _install_clients(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, MagicMock | None]) -> None:
    """Stub the module-level client cache used by _resolve_client."""
    import shoreguard.services.gateway as gateway_mod

    pool = {}
    for name, client in mapping.items():
        entry = SimpleNamespace(client=client, backoff=0.0, last_attempt=0.0)
        pool[name] = entry
    monkeypatch.setattr(gateway_mod, "_clients", pool, raising=True)


# ---------------------------------------------------------------------------
# run_once outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skipped_not_due_when_cert_has_plenty_of_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cert with 30 days left is not rotated under a 7-day threshold."""
    gw_svc = _gateway_service([{"name": "gw1"}])
    _install_clients(
        monkeypatch,
        {"gw1": _client(_cert_info(seconds_until_expiry=30 * 86400))},
    )
    svc = CertRotationService(gw_svc, threshold_days=7, max_retries=3)
    outcomes = await svc.run_once()
    assert outcomes["skipped_not_due"] == 1
    assert outcomes["success"] == 0


@pytest.mark.asyncio
async def test_skipped_no_cert_for_plaintext_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plaintext gateway (``cert_info is None``) is a no-op."""
    gw_svc = _gateway_service([{"name": "gw1"}])
    _install_clients(monkeypatch, {"gw1": _client(cert_info=None)})
    svc = CertRotationService(gw_svc, threshold_days=7, max_retries=3)
    outcomes = await svc.run_once()
    assert outcomes["skipped_no_cert"] == 1


@pytest.mark.asyncio
async def test_skipped_no_cert_when_client_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    """No live client in the pool also counts as skipped_no_cert."""
    gw_svc = _gateway_service([{"name": "gw1"}])
    _install_clients(monkeypatch, {"gw1": None})
    svc = CertRotationService(gw_svc, threshold_days=7, max_retries=3)
    outcomes = await svc.run_once()
    assert outcomes["skipped_no_cert"] == 1


@pytest.mark.asyncio
async def test_rotates_when_below_threshold_and_creds_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cert with 3 days left triggers rotation using the registry creds."""
    client = _client(_cert_info(seconds_until_expiry=3 * 86400))
    gw_svc = _gateway_service(
        [{"name": "gw1"}],
        creds={
            "gw1": {
                "ca_cert": b"ca-bytes",
                "client_cert": b"client-bytes",
                "client_key": b"key-bytes",
            }
        },
    )
    _install_clients(monkeypatch, {"gw1": client})
    # Don't actually fire webhooks or audits.
    monkeypatch.setattr(
        "shoreguard.services.cert_rotation.fire_webhook",
        AsyncMock(),
    )
    svc = CertRotationService(gw_svc, threshold_days=7, max_retries=3)
    outcomes = await svc.run_once()
    assert outcomes["success"] == 1
    client.reload_credentials.assert_called_once_with(
        ca_cert=b"ca-bytes",
        client_cert=b"client-bytes",
        client_key=b"key-bytes",
    )


@pytest.mark.asyncio
async def test_failure_after_retries_fires_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """All retries exhausted → ``failure`` outcome + webhook fired."""
    client = _client(_cert_info(seconds_until_expiry=3 * 86400))
    client.reload_credentials.side_effect = RuntimeError("bad cert")
    gw_svc = _gateway_service(
        [{"name": "gw1"}],
        creds={
            "gw1": {
                "ca_cert": b"ca",
                "client_cert": b"c",
                "client_key": b"k",
            }
        },
    )
    _install_clients(monkeypatch, {"gw1": client})
    # Avoid real backoff delays during retries.
    monkeypatch.setattr(
        "shoreguard.services.cert_rotation.asyncio.sleep",
        AsyncMock(),
    )
    webhook_mock = AsyncMock()
    monkeypatch.setattr(
        "shoreguard.services.cert_rotation.fire_webhook",
        webhook_mock,
    )
    svc = CertRotationService(gw_svc, threshold_days=7, max_retries=2)
    outcomes = await svc.run_once()
    assert outcomes["failure"] == 1
    assert client.reload_credentials.call_count == 2
    webhook_mock.assert_awaited_once()
    await_args = webhook_mock.await_args
    assert await_args is not None
    event, payload = await_args[0]
    assert event == "gateway.cert_rotation_failed"
    assert payload["gateway"] == "gw1"
    assert payload["retries"] == 2
    assert "bad cert" in payload["reason"]


@pytest.mark.asyncio
async def test_failure_when_registry_has_no_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing registry creds produce a ``failure`` outcome, not a crash."""
    client = _client(_cert_info(seconds_until_expiry=2 * 86400))
    gw_svc = _gateway_service([{"name": "gw1"}], creds={})
    _install_clients(monkeypatch, {"gw1": client})
    monkeypatch.setattr(
        "shoreguard.services.cert_rotation.asyncio.sleep",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "shoreguard.services.cert_rotation.fire_webhook",
        AsyncMock(),
    )
    svc = CertRotationService(gw_svc, threshold_days=7, max_retries=1)
    outcomes = await svc.run_once()
    assert outcomes["failure"] == 1
    client.reload_credentials.assert_not_called()


@pytest.mark.asyncio
async def test_gateways_without_name_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed registry row without a ``name`` field is skipped silently."""
    gw_svc = _gateway_service([{"name": ""}])
    _install_clients(monkeypatch, {})
    svc = CertRotationService(gw_svc, threshold_days=7, max_retries=1)
    outcomes = await svc.run_once()
    # No outcome recorded because the row had no name.
    assert sum(outcomes.values()) == 0
