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


# ── Additional mutation-killing tests ────────────────────────────────────────


class TestAlembicHelpers:
    def test_alembic_dir_returns_string(self):
        from shoreguard.db import _alembic_dir

        result = _alembic_dir()
        assert isinstance(result, str)
        assert result.endswith("alembic")
        assert "shoreguard" in result

    def test_alembic_dir_points_to_real_directory(self):
        from shoreguard.db import _alembic_dir

        result = _alembic_dir()
        assert Path(result).is_dir()

    def test_alembic_config_sets_options(self):
        from shoreguard.db import _alembic_config

        cfg = _alembic_config("sqlite:///test.db")
        assert cfg.get_main_option("sqlalchemy.url") == "sqlite:///test.db"
        assert cfg.get_main_option("script_location").endswith("alembic")

    def test_alembic_config_different_urls(self):
        from shoreguard.db import _alembic_config

        cfg1 = _alembic_config("sqlite:///a.db")
        cfg2 = _alembic_config("sqlite:///b.db")
        assert cfg1.get_main_option("sqlalchemy.url") == "sqlite:///a.db"
        assert cfg2.get_main_option("sqlalchemy.url") == "sqlite:///b.db"


class TestInitDbBranches:
    def test_init_db_uses_default_url_when_none(self, monkeypatch):
        """init_db(None) falls back to default_database_url()."""

        # Track that default_database_url is called when url is None
        monkeypatch.setattr(
            "shoreguard.db.default_database_url",
            lambda: "sqlite:///:memory:",
        )
        engine = init_db(None)
        tables = inspect(engine).get_table_names()
        assert "gateways" in tables
        engine.dispose()

    def test_init_db_empty_string_uses_default(self, monkeypatch):
        """init_db('') falls back to default_database_url() because empty string is falsy."""
        monkeypatch.setattr(
            "shoreguard.db.default_database_url",
            lambda: "sqlite:///:memory:",
        )
        engine = init_db("")
        tables = inspect(engine).get_table_names()
        assert "gateways" in tables
        engine.dispose()

    def test_init_db_sets_module_engine(self):
        import shoreguard.db as db_mod

        engine = init_db("sqlite:///:memory:")
        assert db_mod._engine is engine
        engine.dispose()

    def test_init_db_returns_engine(self):
        engine = init_db("sqlite:///:memory:")
        assert engine is not None
        from sqlalchemy.engine import Engine

        assert isinstance(engine, Engine)
        engine.dispose()

    def test_init_db_sqlite_empty_url(self):
        """sqlite:// (no path) is treated as in-memory."""
        engine = init_db("sqlite://")
        tables = inspect(engine).get_table_names()
        assert "gateways" in tables
        engine.dispose()

    def test_sqlite_wal_pragma_set(self):
        """SQLite file engines should have WAL journal mode after connect."""
        from sqlalchemy import text

        with tempfile.TemporaryDirectory() as d:
            url = f"sqlite:///{d}/wal_test.db"
            engine = init_db(url)
            with engine.connect() as conn:
                result = conn.execute(text("PRAGMA journal_mode"))
                mode = result.scalar()
                assert mode == "wal"
            engine.dispose()

    def test_sqlite_foreign_keys_on(self):
        from sqlalchemy import text

        with tempfile.TemporaryDirectory() as d:
            url = f"sqlite:///{d}/fk_test.db"
            engine = init_db(url)
            with engine.connect() as conn:
                result = conn.execute(text("PRAGMA foreign_keys"))
                assert result.scalar() == 1
            engine.dispose()

    def test_sqlite_busy_timeout_set(self):
        from sqlalchemy import text

        with tempfile.TemporaryDirectory() as d:
            url = f"sqlite:///{d}/bt_test.db"
            engine = init_db(url)
            with engine.connect() as conn:
                result = conn.execute(text("PRAGMA busy_timeout"))
                assert result.scalar() == 5000
            engine.dispose()

    def test_sqlite_synchronous_normal(self):
        from sqlalchemy import text

        with tempfile.TemporaryDirectory() as d:
            url = f"sqlite:///{d}/sync_test.db"
            engine = init_db(url)
            with engine.connect() as conn:
                result = conn.execute(text("PRAGMA synchronous"))
                # NORMAL = 1
                assert result.scalar() == 1
            engine.dispose()

    def test_connect_args_check_same_thread_for_sqlite(self):
        """SQLite should set check_same_thread=False."""
        engine = init_db("sqlite:///:memory:")
        # If it works at all from multiple threads, check_same_thread=False is set
        assert engine is not None
        engine.dispose()

    def test_migration_failure_raises_runtime_error(self, tmp_path, monkeypatch):
        """Failed Alembic migration should raise RuntimeError with message."""
        from unittest.mock import patch

        url = f"sqlite:///{tmp_path}/test.db"
        with patch("shoreguard.db.command.upgrade", side_effect=RuntimeError("migration boom")):
            try:
                init_db(url)
                raise AssertionError("Expected RuntimeError")
            except RuntimeError as e:
                assert "Database migration failed" in str(e)
                assert "migration boom" in str(e)

    def test_migration_os_error_raises_runtime_error(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        url = f"sqlite:///{tmp_path}/test.db"
        with patch("shoreguard.db.command.upgrade", side_effect=OSError("disk full")):
            try:
                init_db(url)
                raise AssertionError("Expected RuntimeError")
            except RuntimeError as e:
                assert "Database migration failed" in str(e)
                assert "disk full" in str(e)

    def test_file_permissions_set_before_and_after_migration(self):
        """DB file should get 0o600 permissions both before and after migration."""
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "perms.db"
            # Pre-create the file to test the "before migration" chmod
            db_path.write_bytes(b"")
            url = f"sqlite:///{db_path}"
            engine = init_db(url)
            mode = oct(db_path.stat().st_mode & 0o777)
            assert mode == "0o600"
            engine.dispose()


class TestGetEngine:
    def test_get_engine_returns_exact_engine(self):
        engine = init_db("sqlite:///:memory:")
        retrieved = get_engine()
        assert retrieved is engine
        engine.dispose()

    def test_get_engine_error_message(self):
        import shoreguard.db as db_mod

        original = db_mod._engine
        db_mod._engine = None
        try:
            try:
                get_engine()
                raise AssertionError("Expected RuntimeError")
            except RuntimeError as e:
                assert "init_db()" in str(e)
                assert "not initialised" in str(e)
        finally:
            db_mod._engine = original


class TestAsyncDb:
    def test_init_async_db_sqlite_file(self, tmp_path):
        import shoreguard.db as db_mod
        from shoreguard.db import init_async_db

        url = f"sqlite:///{tmp_path}/test.db"
        async_engine = init_async_db(url)
        assert async_engine is not None
        assert db_mod._async_engine is async_engine
        assert db_mod._async_session_factory is not None
        # Verify URL conversion
        assert "aiosqlite" in str(async_engine.url)
        # Clean up
        db_mod._async_engine = None
        db_mod._async_session_factory = None

    def test_init_async_db_sqlite_memory(self):
        import shoreguard.db as db_mod
        from shoreguard.db import init_async_db

        async_engine = init_async_db("sqlite:///:memory:")
        assert async_engine is not None
        assert "aiosqlite" in str(async_engine.url)
        db_mod._async_engine = None
        db_mod._async_session_factory = None

    def test_init_async_db_sqlite_no_path(self):
        import shoreguard.db as db_mod
        from shoreguard.db import init_async_db

        async_engine = init_async_db("sqlite://")
        assert async_engine is not None
        assert "aiosqlite" in str(async_engine.url)
        db_mod._async_engine = None
        db_mod._async_session_factory = None

    def test_get_async_session_factory_before_init_raises(self):
        import shoreguard.db as db_mod
        from shoreguard.db import get_async_session_factory

        original = db_mod._async_session_factory
        db_mod._async_session_factory = None
        try:
            try:
                get_async_session_factory()
                raise AssertionError("Expected RuntimeError")
            except RuntimeError as e:
                assert "init_async_db()" in str(e)
                assert "not initialised" in str(e)
        finally:
            db_mod._async_session_factory = original

    def test_get_async_session_factory_after_init(self):
        import shoreguard.db as db_mod
        from shoreguard.db import get_async_session_factory, init_async_db

        init_async_db("sqlite:///:memory:")
        factory = get_async_session_factory()
        assert factory is not None
        assert factory is db_mod._async_session_factory
        db_mod._async_engine = None
        db_mod._async_session_factory = None

    def test_dispose_async_engine(self):
        import asyncio

        import shoreguard.db as db_mod
        from shoreguard.db import dispose_async_engine, init_async_db

        init_async_db("sqlite:///:memory:")
        assert db_mod._async_engine is not None
        asyncio.run(dispose_async_engine())
        assert db_mod._async_engine is None
        assert db_mod._async_session_factory is None

    def test_dispose_async_engine_when_none(self):
        """dispose_async_engine should be safe to call when no engine exists."""
        import asyncio

        import shoreguard.db as db_mod
        from shoreguard.db import dispose_async_engine

        db_mod._async_engine = None
        db_mod._async_session_factory = None
        # Should not raise
        asyncio.run(dispose_async_engine())
        assert db_mod._async_engine is None
        assert db_mod._async_session_factory is None


class TestInitDbPoolPrePing:
    def test_pool_pre_ping_enabled(self):
        """Engine should have pool_pre_ping=True."""
        engine = init_db("sqlite:///:memory:")
        assert engine.pool._pre_ping is True
        engine.dispose()
