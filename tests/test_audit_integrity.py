"""Tests for append-only enforcement and offline audit export."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shoreguard.models import AuditEntry, Base
from shoreguard.services.audit import AuditIntegrityError, AuditService


@pytest.fixture
def audit_svc():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    svc = AuditService(factory)
    yield svc
    engine.dispose()


def _insert_entry(svc: AuditService) -> None:
    svc.log(
        actor="admin@test.com",
        actor_role="admin",
        action="sandbox.create",
        resource_type="sandbox",
        resource_id="sb-1",
    )


def test_update_is_blocked(audit_svc: AuditService) -> None:
    _insert_entry(audit_svc)
    factory = audit_svc._session_factory
    with factory() as session:
        entry = session.query(AuditEntry).first()
        assert entry is not None
        entry.actor = "tampered@evil.com"
        with pytest.raises(AuditIntegrityError, match="UPDATE is not allowed"):
            session.commit()


def test_delete_outside_cleanup_is_blocked(audit_svc: AuditService) -> None:
    _insert_entry(audit_svc)
    factory = audit_svc._session_factory
    with factory() as session:
        entry = session.query(AuditEntry).first()
        assert entry is not None
        session.delete(entry)
        with pytest.raises(AuditIntegrityError, match="DELETE only allowed"):
            session.commit()


def test_cleanup_can_delete(audit_svc: AuditService) -> None:
    # Insert an old entry directly so cleanup's threshold matches
    factory = audit_svc._session_factory
    with factory() as session:
        session.add(
            AuditEntry(
                timestamp=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
                actor="old@test.com",
                actor_role="admin",
                action="x",
                resource_type="y",
            )
        )
        session.commit()
    removed = audit_svc.cleanup(older_than_days=1)
    assert removed == 1
    assert audit_svc.list() == []


def test_cleanup_bypass_is_scoped(audit_svc: AuditService) -> None:
    """After cleanup() returns, deletion must be blocked again."""
    _insert_entry(audit_svc)
    # cleanup with no old rows — should return 0 without raising
    audit_svc.cleanup(older_than_days=3650)
    # Now try a normal delete — must still raise
    factory = audit_svc._session_factory
    with factory() as session:
        entry = session.query(AuditEntry).first()
        session.delete(entry)
        with pytest.raises(AuditIntegrityError):
            session.commit()


def test_export_json_serializes_entries(audit_svc: AuditService) -> None:
    _insert_entry(audit_svc)
    out = audit_svc.export_json()
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["actor"] == "admin@test.com"
    assert data[0]["action"] == "sandbox.create"


def test_audit_export_cli_writes_three_files(
    audit_svc: AuditService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shoreguard audit export`` writes export, sha256 digest, and manifest."""
    _insert_entry(audit_svc)

    # The CLI calls get_engine() which in tests is not initialised.  We
    # patch the CLI's lazy imports so it uses our in-memory fixture.
    from shoreguard.api import cli_audit

    engine = audit_svc._session_factory.kw["bind"]

    def _fake_get_engine():
        return engine

    def _fake_sessionmaker(*_args, **_kwargs):
        return audit_svc._session_factory

    monkeypatch.setattr(cli_audit, "audit_export", cli_audit.audit_export)
    import shoreguard.db as _db

    monkeypatch.setattr(_db, "get_engine", _fake_get_engine)
    monkeypatch.setattr(_db, "init_db", lambda *_a, **_kw: engine)
    import sqlalchemy.orm as _orm

    monkeypatch.setattr(_orm, "sessionmaker", lambda **_kw: audit_svc._session_factory)

    from typer.testing import CliRunner

    from shoreguard.api.cli import cli

    out_file = tmp_path / "audit.json"
    result = CliRunner().invoke(
        cli,
        ["audit", "export", "--out", str(out_file), "--format", "json"],
    )
    assert result.exit_code == 0, result.output

    sha_file = out_file.with_name("audit.json.sha256")
    manifest_file = out_file.with_name("audit.json.manifest.json")

    assert out_file.exists()
    assert sha_file.exists()
    assert manifest_file.exists()

    # SHA256 digest matches the actual file
    expected_digest = hashlib.sha256(out_file.read_bytes()).hexdigest()
    sha_content = sha_file.read_text().strip()
    assert sha_content.startswith(expected_digest)
    assert sha_content.endswith("audit.json")

    # Manifest references the correct file and entry count
    manifest = json.loads(manifest_file.read_text())
    assert manifest["file"] == "audit.json"
    assert manifest["sha256"] == expected_digest
    assert manifest["entries"] == 1
    assert manifest["format"] == "json"

    # File permissions are 0600
    assert oct(out_file.stat().st_mode)[-3:] == "600"
    assert oct(sha_file.stat().st_mode)[-3:] == "600"
    assert oct(manifest_file.stat().st_mode)[-3:] == "600"
