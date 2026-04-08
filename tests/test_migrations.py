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
from sqlalchemy.exc import OperationalError


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
    "webhooks",
    "webhook_deliveries",
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
    "description",
    "labels_json",
    "registered_at",
    "last_seen",
    "last_status",
}

EXPECTED_USER_COLUMNS = {
    "id",
    "email",
    "hashed_password",
    "role",
    "is_active",
    "invite_token_hash",
    "created_at",
    "oidc_provider",
    "oidc_sub",
}

EXPECTED_USER_GATEWAY_ROLE_COLUMNS = {
    "id",
    "user_id",
    "gateway_id",
    "role",
}

EXPECTED_SP_GATEWAY_ROLE_COLUMNS = {
    "id",
    "sp_id",
    "gateway_id",
    "role",
}

EXPECTED_SP_COLUMNS = {
    "id",
    "name",
    "key_hash",
    "key_prefix",
    "role",
    "created_by",
    "created_at",
    "last_used",
    "expires_at",
}

EXPECTED_WEBHOOK_DELIVERY_COLUMNS = {
    "id",
    "webhook_id",
    "event_type",
    "payload_json",
    "status",
    "response_code",
    "error_message",
    "attempt",
    "created_at",
    "delivered_at",
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
        insp = inspect(engine)

        for table_name, expected_cols in [
            ("gateways", EXPECTED_GATEWAY_COLUMNS),
            ("users", EXPECTED_USER_COLUMNS),
            ("user_gateway_roles", EXPECTED_USER_GATEWAY_ROLE_COLUMNS),
            ("sp_gateway_roles", EXPECTED_SP_GATEWAY_ROLE_COLUMNS),
            ("service_principals", EXPECTED_SP_COLUMNS),
            ("webhook_deliveries", EXPECTED_WEBHOOK_DELIVERY_COLUMNS),
        ]:
            columns = {c["name"] for c in insp.get_columns(table_name)}
            assert columns == expected_cols, (
                f"Column mismatch for {table_name}.\nExpected: {expected_cols}\nActual: {columns}"
            )

        engine.dispose()


def test_migrations_sqlite_downgrade():
    """Downgrading from head drops all tables cleanly."""
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test_downgrade.db"
        cfg = _alembic_config(url)
        command.upgrade(cfg, "head")

        command.downgrade(cfg, "base")

        actual = _current_revision(url)
        assert actual is None

        engine = create_engine(url)
        tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert tables == set(), f"Tables remaining after downgrade: {tables}"
        engine.dispose()


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
    except OperationalError, OSError, ImportError:
        pytest.skip("PostgreSQL not available")

    _run_all_migrations(url)

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES.issubset(tables), f"Missing tables: {EXPECTED_TABLES - tables}"

        columns = {c["name"] for c in inspect(engine).get_columns("gateways")}
        assert columns == EXPECTED_GATEWAY_COLUMNS
    finally:
        all_tables = inspect(engine).get_table_names()
        with engine.begin() as conn:
            for table in all_tables:
                conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
        engine.dispose()
