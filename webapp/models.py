"""PostgreSQL ORM models -- the single schema shared by the CLI (newsagent)
and the webapp.

    newspapers       one row per ingested newspaper edition
    pages            one row per PDF page, raw extracted text (CLI staging:
                     builds chat prompts, and article bodies are sliced out
                     of it by anchor)
    articles         one row per article -- original text, summary, category
    topics           distinct topic names, mirrored from the CLI's topics.txt
    article_topics   which topics each article was tagged with, with confidence

Every filterable field the browser UI exposes (newspaper, category, topic,
published_at, and full-text search over headline/summary/body) is a plain
indexed column or a join, not a JSON blob -- so filtering is a normal SQL
WHERE clause.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import (
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

_SEARCH_VECTOR_EXPR = (
    "to_tsvector('english', coalesce(headline,'') || ' ' || "
    "coalesce(original_text,'') || ' ' || coalesce(summary_text,''))"
)


class Newspaper(Base):
    __tablename__ = "newspapers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    edition_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    # CLI-only bookkeeping (unused by the webapp itself, kept so the CLI's
    # 'editions'/'stats' commands have somewhere to live now that there is no
    # separate SQLite store).
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_processed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    articles: Mapped[list["Article"]] = relationship(
        back_populates="newspaper", cascade="all, delete-orphan"
    )
    pages: Mapped[list["Page"]] = relationship(
        back_populates="newspaper", cascade="all, delete-orphan"
    )


class Page(Base):
    """The extracted text of one PDF page, kept verbatim.

    Storing this is what makes the CLI's manual chat workflow possible: the
    prompt is built from it, and article bodies are sliced out of it when a
    pasted response comes back. Not used by the browser UI.
    """

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("newspaper_id", "page_number", name="uq_newspaper_page"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    newspaper_id: Mapped[int] = mapped_column(
        ForeignKey("newspapers.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    column_text: Mapped[str] = mapped_column(Text, default="")
    layout_text: Mapped[str] = mapped_column(Text, default="")
    has_text_layer: Mapped[int] = mapped_column(Integer, default=1)

    newspaper: Mapped[Newspaper] = relationship(back_populates="pages")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    # CLI-only: mirrored from topics.txt (newsagent.topics.sync_topics).
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[int] = mapped_column(Integer, default=1)

    article_links: Mapped[list["ArticleTopic"]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )

    @property
    def keywords(self) -> list[str]:
        return json.loads(self.keywords_json or "[]")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint(
            "newspaper_id", "page_number", "headline", name="uq_newspaper_page_headline"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    newspaper_id: Mapped[int] = mapped_column(
        ForeignKey("newspapers.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    headline: Mapped[str] = mapped_column(Text)
    byline: Mapped[str | None] = mapped_column(Text, nullable=True)
    section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    original_text: Mapped[str] = mapped_column(Text)
    # Rough word count of original_text (CLI display only).
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bullets: Mapped[list] = mapped_column(JSONB, default=list)
    entities: Mapped[list] = mapped_column(JSONB, default=list)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_minutes: Mapped[int] = mapped_column(Integer, default=1)
    # Which model produced the summary (blank for the CLI's manual/chat-paste
    # workflow, which makes no API call).
    summary_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # How original_text was obtained upstream: 'anchor', 'llm', or 'unmatched'.
    body_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    # NULL = unread. Set to the time it was marked read; cleared to mark
    # unread again. Indexed because "unread only" is a common list filter.
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    # NULL = not a favorite. Set to the time it was favorited; cleared to
    # unfavorite. Indexed because "favorites only" is a common list filter.
    favorited_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    # Full-text index over headline + original_text + summary_text, kept in
    # sync by Postgres itself (STORED generated column) -- see queries.py's
    # search_articles for how it's queried via plainto_tsquery/ts_rank.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(_SEARCH_VECTOR_EXPR, persisted=True)
    )

    newspaper: Mapped[Newspaper] = relationship(back_populates="articles")
    topic_links: Mapped[list["ArticleTopic"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


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
    # Provenance: 'llm', 'llm+keyword', 'chat', 'chat+keyword', or 'manual'
    # (added by a person editing tags in the webapp).
    matched_by: Mapped[str] = mapped_column(String(20), default="llm")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    article: Mapped[Article] = relationship(back_populates="topic_links")
    topic: Mapped[Topic] = relationship(back_populates="article_links")
