"""Database access -- a thin adapter over the webapp's PostgreSQL schema.

The CLI and the webapp share one Postgres database and one schema
(webapp/models.py); this module just re-exports the pieces newsagent needs
under the names the rest of the package already uses, plus the CLI-only
session-management helpers.

`Edition` is an alias for `webapp.models.Newspaper` -- kept so CLI vocabulary
("editions", `--edition`, `Edition.edition_date`) doesn't need to change, even
though the underlying table is `newspapers`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.database import SessionLocal, init_db
from webapp.models import Article, ArticleTopic, Newspaper as Edition, Page, Topic

__all__ = [
    "Article",
    "ArticleTopic",
    "Edition",
    "Page",
    "Topic",
    "session_scope",
    "sha256_file",
    "find_edition_by_hash",
    "counts",
]

_initialised = False


def _ensure_initialised() -> None:
    global _initialised
    if not _initialised:
        init_db()
        _initialised = True


@contextmanager
def session_scope() -> Iterator[Session]:
    _ensure_initialised()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def find_edition_by_hash(session: Session, sha: str) -> Edition | None:
    return session.scalar(select(Edition).where(Edition.file_hash == sha))


def counts(session: Session) -> dict[str, int]:
    return {
        "editions": session.scalar(select(func.count()).select_from(Edition)) or 0,
        "articles": session.scalar(select(func.count()).select_from(Article)) or 0,
        "summaries": session.scalar(
            select(func.count())
            .select_from(Article)
            .where(Article.summary_text.is_not(None))
        )
        or 0,
        "topics": session.scalar(select(func.count()).select_from(Topic)) or 0,
        "tags": session.scalar(select(func.count()).select_from(ArticleTopic)) or 0,
    }
