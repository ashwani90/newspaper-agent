"""PostgreSQL ORM models for the web application.

    newspapers       one row per newspaper edition pushed from the CLI
    articles         one row per article -- original text, summary, category
    topics           distinct topic names seen across all articles
    article_topics   which topics each article was tagged with, with confidence

Every filterable field the browser UI exposes (newspaper, category, topic,
published_at, and full-text search over headline/summary/body) is a plain
indexed column or a join, not a JSON blob -- so filtering is a normal SQL
WHERE clause.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Newspaper(Base):
    __tablename__ = "newspapers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    edition_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    articles: Mapped[list["Article"]] = relationship(
        back_populates="newspaper", cascade="all, delete-orphan"
    )


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)

    article_links: Mapped[list["ArticleTopic"]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )


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
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bullets: Mapped[list] = mapped_column(JSONB, default=list)
    entities: Mapped[list] = mapped_column(JSONB, default=list)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_minutes: Mapped[int] = mapped_column(Integer, default=1)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
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
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    article: Mapped[Article] = relationship(back_populates="topic_links")
    topic: Mapped[Topic] = relationship(back_populates="article_links")
