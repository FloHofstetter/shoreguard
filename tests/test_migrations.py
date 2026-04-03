"""Tests that run all Alembic migrations in order and verify final schema."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def _alembic_config(url: str) -> AlembicConfig:
    alembic_dir = Path(__file__).parent.parent / "shoreguard" / "alembic"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _run_all_migrations(url: str) -> None:
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")


def _current_revision(url: str) -> str | None:
    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


EXPECTED_TABLES = {
    "gateways",
    "users",
    "service_principals",
    "audit_log",
    "user_gateway_roles",
    "sp_gateway_roles",
    "alembic_version",
}

EXPECTED_GATEWAY_COLUMNS = {
    "id",
    "name",
    "endpoint",
    "scheme",
    "auth_mode",
    "ca_cert",
    "client_cert",
    "client_key",
    "metadata_json",
    "registered_at",
    "last_seen",
    "last_status",
}


def test_migrations_sqlite_fresh_db():
    """All migrations apply cleanly on a fresh SQLite file database."""
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test.db"
        _run_all_migrations(url)
        engine = create_engine(url)
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES.issubset(tables), f"Missing tables: {EXPECTED_TABLES - tables}"
        engine.dispose()


def test_migrations_sqlite_head_revision():
    """After upgrade to head, alembic_version matches the latest script."""
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test.db"
        cfg = _alembic_config(url)
        command.upgrade(cfg, "head")

        script = ScriptDirectory.from_config(cfg)
        expected_head = script.get_current_head()
        actual = _current_revision(url)
        assert actual == expected_head, f"Expected revision {expected_head}, got {actual}"


def test_migrations_sqlite_schema_matches_models():
    """The schema after all migrations matches the expected columns from models.py."""
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test.db"
        _run_all_migrations(url)
        engine = create_engine(url)
        columns = {c["name"] for c in inspect(engine).get_columns("gateways")}
        assert columns == EXPECTED_GATEWAY_COLUMNS, (
            f"Column mismatch.\nExpected: {EXPECTED_GATEWAY_COLUMNS}\nActual: {columns}"
        )
        engine.dispose()


def test_migrations_sqlite_incremental():
    """Applying migrations one at a time reaches the same final state as upgrade head."""
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test_incremental.db"
        cfg = _alembic_config(url)
        script = ScriptDirectory.from_config(cfg)

        revisions = [rev.revision for rev in script.walk_revisions()]
        revisions.reverse()  # oldest first

        for rev in revisions:
            command.upgrade(cfg, rev)

        actual = _current_revision(url)
        expected = script.get_current_head()
        assert actual == expected

        engine = create_engine(url)
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES.issubset(tables)
        engine.dispose()


def test_migrations_sqlite_downgrade_irreversible():
    """Migration 007 raises NotImplementedError on downgrade (by design)."""
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test_downgrade_007.db"
        cfg = _alembic_config(url)
        command.upgrade(cfg, "head")

        with pytest.raises(NotImplementedError):
            command.downgrade(cfg, "-1")


def test_migrations_sqlite_downgrade_reversible():
    """Downgrading a reversible migration (006 → 005) succeeds without error."""
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test_downgrade_006.db"
        cfg = _alembic_config(url)
        command.upgrade(cfg, "006")

        command.downgrade(cfg, "-1")

        actual = _current_revision(url)
        assert actual == "005"


@pytest.mark.postgres
def test_migrations_postgres_fresh_db():
    """All migrations apply cleanly on a fresh PostgreSQL database."""
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://shoreguard:shoreguard@localhost:5432/shoreguard_test",
    )
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        pytest.skip("PostgreSQL not available")

    _run_all_migrations(url)

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables), f"Missing tables: {EXPECTED_TABLES - tables}"

    columns = {c["name"] for c in inspect(engine).get_columns("gateways")}
    assert columns == EXPECTED_GATEWAY_COLUMNS
    engine.dispose()
