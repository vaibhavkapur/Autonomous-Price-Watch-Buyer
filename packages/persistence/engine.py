from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from .schema import metadata


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Create an engine with correct transactional semantics.

    SQLite: WAL, busy timeout and explicit ``BEGIN IMMEDIATE`` so that
    concurrent workers serialize on write transactions instead of raising
    "database is locked" mid-transaction. PostgreSQL uses the defaults.
    """
    if url.startswith("sqlite"):
        in_memory = url in ("sqlite://", "sqlite:///:memory:")
        connect_args = {"check_same_thread": False, "timeout": 30}
        if in_memory:
            engine = create_engine(url, echo=echo, connect_args=connect_args, poolclass=StaticPool)
        else:
            path = url.replace("sqlite:///", "", 1)
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, echo=echo, connect_args=connect_args)

        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_connection, _record):  # pragma: no cover - driver glue
            # Let SQLAlchemy control transactions explicitly.
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            if not in_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn):  # pragma: no cover - driver glue
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        return engine
    return create_engine(url, echo=echo, pool_pre_ping=True)


def init_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def engine_from_env() -> Engine:
    url = os.environ.get("DATABASE_URL", "sqlite:///data/pricewatch.sqlite3")
    return make_engine(url)
