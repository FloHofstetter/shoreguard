"""Database engine, session factory, and embedded Alembic migrations."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from shoreguard.config import default_database_url

logger = logging.getLogger(__name__)

_engine: Engine | None = None


def _alembic_dir() -> str:
    """Return the path to the alembic directory shipped inside the package.

    Returns:
        str: Absolute path to the embedded alembic directory.
    """
    return str(Path(__file__).parent.parent / "alembic")


def _alembic_config(url: str) -> AlembicConfig:
    """Build an Alembic config pointing at our embedded migrations.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        AlembicConfig: Configured Alembic config instance.
    """
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _alembic_dir())
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


# Final revision of the pre-squash migration chain (the v0.37 head).
# Databases sitting exactly here are schema-identical to the squashed
# v2 baseline and are stamped instead of migrated.
_PRE_SQUASH_HEAD = "017"
_BASELINE_REVISION = "v2_baseline"


def _maybe_stamp_v2_baseline(engine: Engine, cfg: AlembicConfig) -> None:
    """Stamp a v0.37 database to the squashed v2 baseline revision.

    The 17-step pre-v0.38 migration chain was squashed into a single
    baseline. A database whose ``alembic_version`` sits at the old head
    has exactly the baseline schema, so it is stamped without running
    DDL. Older revisions cannot be upgraded by this version anymore.

    Args:
        engine: Engine connected to the target database.
        cfg: Alembic config pointing at the embedded migrations.

    Raises:
        RuntimeError: If the database is at a pre-v0.37 schema revision.
    """
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except OperationalError, SQLAlchemyError:
        return  # fresh database — no alembic_version table yet
    if version is None or version == _BASELINE_REVISION:
        return
    if version == _PRE_SQUASH_HEAD:
        logger.info(
            "Stamping v0.37 database (revision %s) to squashed baseline %s",
            version,
            _BASELINE_REVISION,
        )
        # purge=True clears the old version row without resolving the
        # (now deleted) pre-squash revision in the script directory.
        command.stamp(cfg, _BASELINE_REVISION, purge=True)
        return
    script = ScriptDirectory.from_config(cfg)
    try:
        script.get_revision(version)
    except CommandError:
        raise RuntimeError(
            f"Database schema revision '{version}' predates ShoreGuard v0.37. "
            "The migration chain was squashed in v0.38 — upgrade to v0.37 "
            "first, then to this version."
        ) from None
    # A revision the current chain knows (baseline or any migration on
    # top of it) — the regular `upgrade head` brings it forward.


def init_db(url: str | None = None) -> Engine:
    """Create the engine, run migrations, and configure the session factory.

    Called once during application startup (FastAPI lifespan).

    Args:
        url: SQLAlchemy database URL. Falls back to ``default_database_url()``.

    Returns:
        Engine: The initialised SQLAlchemy engine.

    Raises:
        RuntimeError: If database migration fails.
    """
    global _engine  # noqa: PLW0603

    database_url = url or default_database_url()

    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if db_path.exists():
            os.chmod(db_path, 0o600)

    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
    is_sqlite = database_url.startswith("sqlite")
    stmt_timeout_ms: int | None = None

    if is_sqlite:
        connect_args["check_same_thread"] = False
    else:
        from shoreguard.settings import get_settings

        db_cfg = get_settings().database
        stmt_timeout_ms = db_cfg.statement_timeout_ms
        engine_kwargs.update(
            pool_size=db_cfg.pool_size,
            max_overflow=db_cfg.max_overflow,
            pool_timeout=db_cfg.pool_timeout,
            pool_recycle=db_cfg.pool_recycle,
        )

    _engine = sa_create_engine(
        database_url,
        connect_args=connect_args,
        **engine_kwargs,
    )

    if is_sqlite:

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn: Any, _connection_record: Any) -> None:
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
            except (OSError, RuntimeError) as e:
                logger.warning("Failed to set SQLite pragmas: %s", e)
            finally:
                cursor.close()

    else:
        stmt_timeout = stmt_timeout_ms

        @event.listens_for(_engine, "connect")
        def _set_pg_options(dbapi_conn: Any, _connection_record: Any) -> None:
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute(f"SET statement_timeout = {stmt_timeout}")
            finally:
                cursor.close()

    if database_url == "sqlite:///:memory:" or database_url == "sqlite://":
        from shoreguard.models import Base

        Base.metadata.create_all(_engine)
    else:
        cfg = _alembic_config(database_url)
        _maybe_stamp_v2_baseline(_engine, cfg)
        from shoreguard.settings import get_settings

        db_cfg2 = get_settings().database
        attempts = max(1, db_cfg2.startup_retry_attempts)
        delay = max(0.1, db_cfg2.startup_retry_delay)
        max_delay = max(delay, db_cfg2.startup_retry_max_delay)

        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                logger.info("Running database migrations (attempt %d/%d)...", attempt, attempts)
                command.upgrade(cfg, "head")
                last_exc = None
                break
            except OperationalError as e:
                last_exc = e
                if attempt >= attempts:
                    break
                logger.warning(
                    "DB not ready (attempt %d/%d): %s — retrying in %.1fs",
                    attempt,
                    attempts,
                    e,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, max_delay)
            except (RuntimeError, OSError, SQLAlchemyError) as e:
                logger.error(
                    "Database migration failed: %s (type=%s)",
                    e,
                    type(e).__name__,
                    exc_info=True,
                )
                raise RuntimeError(f"Database migration failed: {e}") from e

        if last_exc is not None:
            logger.error(
                "Database migration failed after %d attempts: %s",
                attempts,
                last_exc,
                exc_info=True,
            )
            raise RuntimeError(
                f"Database migration failed after {attempts} attempts: {last_exc}"
            ) from last_exc

    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        if db_path.exists():
            os.chmod(db_path, 0o600)

    logger.info("Database initialised (%s)", database_url.split("://")[0])
    return _engine


def get_engine() -> Engine:
    """Return the current engine.

    Returns:
        Engine: The active SQLAlchemy engine.

    Raises:
        RuntimeError: If ``init_db()`` has not been called yet.
    """
    if _engine is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _engine


# ── Async engine (for AsyncOperationService) ─────────────────────────────

_async_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker | None = None


def init_async_db(sync_url: str) -> AsyncEngine:
    """Create an async engine matching the sync database URL.

    Args:
        sync_url: The synchronous SQLAlchemy URL used by :func:`init_db`.

    Returns:
        AsyncEngine: The initialised async engine.
    """
    global _async_engine, _async_session_factory  # noqa: PLW0603

    if sync_url.startswith("sqlite:///"):
        async_url = sync_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    elif sync_url.startswith("sqlite://"):
        async_url = sync_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    else:
        async_url = sync_url

    async_kwargs: dict[str, object] = {"pool_pre_ping": True}
    if not sync_url.startswith("sqlite"):
        from shoreguard.settings import get_settings

        db_cfg = get_settings().database
        async_kwargs.update(
            pool_size=db_cfg.pool_size,
            max_overflow=db_cfg.max_overflow,
            pool_timeout=db_cfg.pool_timeout,
            pool_recycle=db_cfg.pool_recycle,
        )

    _async_engine = create_async_engine(async_url, **async_kwargs)
    _async_session_factory = async_sessionmaker(bind=_async_engine, expire_on_commit=False)

    logger.info("Async database engine initialised (%s)", async_url.split("://")[0])
    return _async_engine


async def dispose_async_engine() -> None:
    """Dispose the async engine and clear the session factory."""
    global _async_engine, _async_session_factory  # noqa: PLW0603
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_factory = None


def get_async_session_factory() -> async_sessionmaker:
    """Return the async session factory.

    Returns:
        async_sessionmaker: The active async session factory.

    Raises:
        RuntimeError: If ``init_async_db()`` has not been called yet.
    """
    if _async_session_factory is None:
        raise RuntimeError("Async database not initialised — call init_async_db() first")
    return _async_session_factory
