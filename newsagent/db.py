"""SQLite schema and access helpers.

Layout:
    editions        one row per ingested PDF
    articles        one row per article found in an edition
    summaries       one row per article (the generated summary)
    topics          your interests, mirrored from topics.txt
    article_topics  which topics each article was tagged with
    articles_fts    FTS5 full-text index over headline + body + summary

articles_fts is a plain (not external-content) FTS5 table refreshed
explicitly by reindex_article, so it stays correct even though the text it
indexes is spread across two tables.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import CONFIG


class Base(DeclarativeBase):
    pass


class Edition(Base):
    __tablename__ = "editions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(200), default="Unknown")
    edition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pdf_path: Mapped[str] = mapped_column(Text)
    pdf_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_processed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    articles: Mapped[list["Article"]] = relationship(
        back_populates="edition", cascade="all, delete-orphan"
    )
    pages: Mapped[list["Page"]] = relationship(
        back_populates="edition", cascade="all, delete-orphan"
    )


class Page(Base):
    """The extracted text of one PDF page, kept verbatim.

    Storing this is free (no model involved) and it is what makes the manual
    chat workflow possible: the prompt is built from it, and article bodies
    are sliced out of it when a pasted response comes back.
    """

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("edition_id", "page_number", name="uq_edition_page"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    edition_id: Mapped[int] = mapped_column(
        ForeignKey("editions.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    column_text: Mapped[str] = mapped_column(Text, default="")
    layout_text: Mapped[str] = mapped_column(Text, default="")
    has_text_layer: Mapped[int] = mapped_column(Integer, default=1)

    edition: Mapped[Edition] = relationship(back_populates="pages")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    edition_id: Mapped[int] = mapped_column(
        ForeignKey("editions.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    headline: Mapped[str] = mapped_column(Text)
    byline: Mapped[str | None] = mapped_column(Text, nullable=True)
    section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    body_text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    # How body_text was obtained: 'llm' (echoed back by the API pipeline),
    # 'anchor' (sliced out of the page text using the anchor line), or
    # 'unmatched' (the anchor could not be located -- see the page text).
    body_source: Mapped[str] = mapped_column(String(20), default="llm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    edition: Mapped[Edition] = relationship(back_populates="articles")
    summary: Mapped["Summary | None"] = relationship(
        back_populates="article", cascade="all, delete-orphan", uselist=False
    )
    topic_links: Mapped[list["ArticleTopic"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), unique=True, index=True
    )
    one_liner: Mapped[str] = mapped_column(Text)
    bullets_json: Mapped[str] = mapped_column(Text, default="[]")
    entities_json: Mapped[str] = mapped_column(Text, default="[]")
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    read_minutes: Mapped[int] = mapped_column(Integer, default=1)
    model: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    article: Mapped[Article] = relationship(back_populates="summary")

    @property
    def bullets(self) -> list[str]:
        return json.loads(self.bullets_json or "[]")

    @property
    def entities(self) -> list[str]:
        return json.loads(self.entities_json or "[]")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    article_links: Mapped[list["ArticleTopic"]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )

    @property
    def keywords(self) -> list[str]:
        return json.loads(self.keywords_json or "[]")


class ArticleTopic(Base):
    __tablename__ = "article_topics"
    __table_args__ = (
        UniqueConstraint("article_id", "topic_id", name="uq_article_topic"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    matched_by: Mapped[str] = mapped_column(String(20), default="llm")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    article: Mapped[Article] = relationship(back_populates="topic_links")
    topic: Mapped[Topic] = relationship(back_populates="article_links")


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    headline, body, summary,
    article_id UNINDEXED,
    tokenize = 'porter unicode61'
);
"""


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        engine = create_engine(CONFIG.db_url, future=True)

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text(FTS_DDL))
        _engine = engine
        _Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _Session is not None
    session = _Session()
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


def reindex_article(session: Session, article: Article) -> None:
    """Refresh one article's row in the FTS index (delete, then insert)."""
    summary_text = ""
    if article.summary is not None:
        parts = [
            article.summary.one_liner or "",
            " ".join(article.summary.bullets),
            article.summary.why_it_matters or "",
        ]
        summary_text = " ".join(p for p in parts if p)
    session.execute(
        text("DELETE FROM articles_fts WHERE article_id = :aid"), {"aid": article.id}
    )
    session.execute(
        text(
            "INSERT INTO articles_fts (headline, body, summary, article_id) "
            "VALUES (:h, :b, :s, :aid)"
        ),
        {
            "h": article.headline or "",
            "b": article.body_text or "",
            "s": summary_text,
            "aid": article.id,
        },
    )


def find_edition_by_hash(session: Session, sha: str) -> Edition | None:
    return session.scalar(select(Edition).where(Edition.pdf_sha256 == sha))


def counts(session: Session) -> dict[str, int]:
    return {
        "editions": session.scalar(select(func.count()).select_from(Edition)) or 0,
        "articles": session.scalar(select(func.count()).select_from(Article)) or 0,
        "summaries": session.scalar(select(func.count()).select_from(Summary)) or 0,
        "topics": session.scalar(select(func.count()).select_from(Topic)) or 0,
        "tags": session.scalar(select(func.count()).select_from(ArticleTopic)) or 0,
    }
