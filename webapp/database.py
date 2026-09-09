"""SQLAlchemy engine/session for PostgreSQL.

Separate from newsagent/db.py (SQLite), which stays in place for the local
extraction staging (edition/page text used to build prompts and slice
article bodies out of anchors). This module is the webapp's own connection
to Postgres, the durable store the browser UI reads from.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import WEB_CONFIG


class Base(DeclarativeBase):
    pass


_engine = create_engine(WEB_CONFIG.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=_engine, future=True, expire_on_commit=False)


def get_engine():
    return _engine


def init_db() -> None:
    """Create all tables if they do not already exist.

    Safe to call on every startup -- create_all is a no-op for tables that
    already exist. Schema changes still need a manual migration or a
    drop/recreate; there is no Alembic setup yet.
    """
    from . import models  # noqa: F401  (import registers the model classes)

    Base.metadata.create_all(_engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
