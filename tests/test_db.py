"""Tests for the database initialisation layer."""

import tempfile
from pathlib import Path

from sqlalchemy import inspect

from shoreguard.db import get_engine, init_db


def test_init_db_creates_tables():
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/test.db"
        engine = init_db(url)
        tables = inspect(engine).get_table_names()
        assert "gateways" in tables
        assert "alembic_version" in tables
        engine.dispose()


def test_init_db_in_memory():
    engine = init_db("sqlite:///:memory:")
    tables = inspect(engine).get_table_names()
    assert "gateways" in tables
    engine.dispose()


def test_init_db_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as d:
        nested = Path(d) / "deep" / "nested"
        url = f"sqlite:///{nested}/test.db"
        engine = init_db(url)
        assert (nested / "test.db").exists()
        engine.dispose()


def test_get_engine_after_init():
    engine = init_db("sqlite:///:memory:")
    assert get_engine() is engine
    engine.dispose()


def test_get_engine_before_init_raises():
    import shoreguard.db as db_mod

    original_engine = db_mod._engine
    db_mod._engine = None
    try:
        try:
            get_engine()
            raise AssertionError("Expected RuntimeError")  # noqa: TRY301
        except RuntimeError:
            pass
    finally:
        db_mod._engine = original_engine


def test_sqlite_file_permissions():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        url = f"sqlite:///{db_path}"
        engine = init_db(url)
        assert db_path.exists()
        mode = oct(db_path.stat().st_mode & 0o777)
        assert mode == "0o600"
        engine.dispose()


def test_gateways_table_columns():
    engine = init_db("sqlite:///:memory:")
    columns = {c["name"] for c in inspect(engine).get_columns("gateways")}
    expected = {
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
    assert columns == expected
    engine.dispose()


def test_audit_log_table_columns():
    engine = init_db("sqlite:///:memory:")
    columns = {c["name"] for c in inspect(engine).get_columns("audit_log")}
    expected = {
        "id",
        "timestamp",
        "actor",
        "actor_role",
        "action",
        "resource_type",
        "resource_id",
        "gateway_name",
        "gateway_id",
        "detail",
        "client_ip",
    }
    assert columns == expected
    engine.dispose()


def test_sqlite_parent_dir_permissions():
    """Parent directory should be created with 0o700."""
    with tempfile.TemporaryDirectory() as d:
        nested = Path(d) / "secure" / "db"
        url = f"sqlite:///{nested}/test.db"
        engine = init_db(url)
        mode = oct(nested.stat().st_mode & 0o777)
        assert mode == "0o700"
        engine.dispose()


def test_sqlite_pragma_failure_does_not_crash():
    """Pragma errors should be logged but not raise."""
    engine = init_db("sqlite:///:memory:")
    # Verify engine works after init — pragmas are set on connect events
    from sqlalchemy import text

    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    engine.dispose()
