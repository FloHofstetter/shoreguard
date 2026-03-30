"""Database engine, session factory, and embedded Alembic migrations."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from shoreguard.config import default_database_url

logger = logging.getLogger(__name__)

_engine: Engine | None = None


def _alembic_dir() -> str:
    """Return the path to the alembic directory shipped inside the package."""
    return str(Path(__file__).parent / "alembic")


def _alembic_config(url: str) -> AlembicConfig:
    """Build an Alembic config pointing at our embedded migrations."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _alembic_dir())
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def init_db(url: str | None = None) -> Engine:
    """Create the engine, run migrations, and configure the session factory.

    Called once during application startup (FastAPI lifespan).
    """
    global _engine  # noqa: PLW0603

    database_url = url or default_database_url()

    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if db_path.exists():
            os.chmod(db_path, 0o600)

    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = sa_create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if database_url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
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

    if database_url == "sqlite:///:memory:" or database_url == "sqlite://":
        from shoreguard.models import Base

        Base.metadata.create_all(_engine)
    else:
        cfg = _alembic_config(database_url)
        try:
            logger.info("Running database migrations...")
            command.upgrade(cfg, "head")
        except (RuntimeError, OSError, SQLAlchemyError) as e:
            logger.error(
                "Database migration failed: %s (type=%s)",
                e,
                type(e).__name__,
                exc_info=True,
            )
            raise RuntimeError(f"Database migration failed: {e}") from e

    if database_url.startswith("sqlite:///"):
        db_path = Path(database_url.removeprefix("sqlite:///"))
        if db_path.exists():
            os.chmod(db_path, 0o600)

    logger.info("Database initialised (%s)", database_url.split("://")[0])
    return _engine


def get_engine() -> Engine:
    """Return the current engine."""
    if _engine is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _engine
