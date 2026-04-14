"""Unit tests for the M28 audit-log export lanes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from shoreguard.services.audit_export import AuditExporter
from shoreguard.settings import AuditSettings


def _mk_settings(**overrides: Any) -> AuditSettings:
    """Return an AuditSettings with every export lane off unless overridden."""
    base: dict[str, Any] = {
        "export_stdout_json": False,
        "export_syslog_enabled": False,
        "export_webhook_enabled": False,
    }
    base.update(overrides)
    return AuditSettings(**base)


_SAMPLE_ENTRY: dict[str, Any] = {
    "id": 42,
    "timestamp": "2026-04-14T10:00:00+00:00",
    "actor": "admin",
    "actor_role": "admin",
    "action": "policy.update",
    "resource_type": "policy",
    "resource_id": "rule-1",
    "gateway": "gw-1",
    "detail": {"before": "deny", "after": "allow"},
    "client_ip": "127.0.0.1",
}


def test_exporter_disabled_by_default():
    exp = AuditExporter(_mk_settings())
    assert exp.enabled is False
    # dispatch is a cheap no-op
    exp.dispatch(_SAMPLE_ENTRY)


def test_stdout_lane_emits_json_line(capsys):
    settings = _mk_settings(export_stdout_json=True)
    exp = AuditExporter(settings)
    assert exp.enabled is True

    exp.dispatch(_SAMPLE_ENTRY)

    # stdlib logger flushes on INFO -> StreamHandler -> sys.stdout.
    captured = capsys.readouterr().out.strip().splitlines()
    assert len(captured) == 1
    parsed = json.loads(captured[0])
    assert parsed["id"] == 42
    assert parsed["action"] == "policy.update"
    assert parsed["detail"] == {"before": "deny", "after": "allow"}


def test_stdout_lane_failure_is_swallowed(monkeypatch, caplog):
    settings = _mk_settings(export_stdout_json=True)
    exp = AuditExporter(settings)

    class _Boom:
        def info(self, *_args: Any, **_kw: Any) -> None:
            raise RuntimeError("disk full")

    exp._stdout_logger = _Boom()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="shoreguard.services.audit_export"):
        exp.dispatch(_SAMPLE_ENTRY)

    assert any("stdout lane failed" in rec.message for rec in caplog.records)


def test_webhook_lane_schedules_fire_on_loop(monkeypatch):
    settings = _mk_settings(export_webhook_enabled=True)

    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_fire(event_type: str, payload: dict[str, Any]) -> None:
        calls.append((event_type, payload))

    import shoreguard.services.webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, "fire_webhook", _fake_fire)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        exp = AuditExporter(settings, loop=loop)
        exp.dispatch(_SAMPLE_ENTRY)
        # Yield so the scheduled coroutine runs.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert len(calls) == 1
    assert calls[0][0] == "audit.entry"
    assert calls[0][1]["id"] == 42


def test_webhook_lane_noop_without_loop():
    """Without a loop reference the webhook lane silently skips."""
    settings = _mk_settings(export_webhook_enabled=True)
    exp = AuditExporter(settings, loop=None)
    # dispatch must not raise
    exp.dispatch(_SAMPLE_ENTRY)


def test_lane_errors_are_isolated(capsys, caplog):
    """A failing lane must not prevent siblings from firing."""
    settings = _mk_settings(export_stdout_json=True, export_syslog_enabled=True)
    exp = AuditExporter(settings)

    class _Boom:
        def info(self, *_a: Any, **_kw: Any) -> None:
            raise RuntimeError("syslog exploded")

    exp._syslog_logger = _Boom()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="shoreguard.services.audit_export"):
        exp.dispatch(_SAMPLE_ENTRY)

    # stdout lane still fired
    captured = capsys.readouterr().out.strip().splitlines()
    assert len(captured) == 1
    assert json.loads(captured[0])["id"] == 42
    # syslog failure was logged
    assert any("syslog lane failed" in rec.message for rec in caplog.records)


def test_audit_service_dispatch_on_write(tmp_path, monkeypatch):
    """Integration: AuditService.log() triggers the exporter."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from shoreguard.models import Base
    from shoreguard.services.audit import AuditService

    db_path = tmp_path / "audit.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    dispatched: list[dict[str, Any]] = []

    class _StubExporter:
        enabled = True

        def dispatch(self, entry: dict[str, Any]) -> None:
            dispatched.append(entry)

    svc = AuditService(session_factory, exporter=_StubExporter())  # type: ignore[arg-type]
    svc.log(
        actor="admin",
        actor_role="admin",
        action="test.action",
        resource_type="resource",
        resource_id="r1",
        detail={"k": "v"},
    )

    assert len(dispatched) == 1
    assert dispatched[0]["action"] == "test.action"
    assert dispatched[0]["detail"] == {"k": "v"}
