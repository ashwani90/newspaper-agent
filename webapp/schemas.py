"""Pydantic request/response schemas for the web API.

ArticleIn / NewspaperIn / BulkIngestRequest are the contract for the general
bulk-import endpoint (POST /api/articles/bulk); the CLI itself writes
directly via SQLAlchemy and doesn't use it. The Out schemas are what the
browser UI reads, and the *Update schemas are what it writes back (read
status, category, topics).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TopicTagIn(BaseModel):
    topic: str
    confidence: float = 0.0
    rationale: str | None = None


class ArticleIn(BaseModel):
    """One article to ingest, as produced by the CLI's parsing pipeline."""

    page_number: int | None = None
    headline: str
    byline: str | None = None
    section: str | None = None
    category: str | None = None
    original_text: str
    summary_text: str | None = None
    bullets: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    why_it_matters: str | None = None
    read_minutes: int = 1
    body_source: str | None = None
    topics: list[TopicTagIn] = Field(default_factory=list)


class NewspaperIn(BaseModel):
    name: str
    edition_date: date | None = None
    source_file: str | None = None
    file_hash: str | None = None


class BulkIngestRequest(BaseModel):
    newspaper: NewspaperIn
    articles: list[ArticleIn]


class TopicTagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    topic: str
    confidence: float
    rationale: str | None = None


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    newspaper_id: int
    newspaper_name: str
    edition_date: date | None = None
    page_number: int | None
    headline: str
    byline: str | None
    section: str | None
    category: str | None
    summary_text: str | None
    bullets: list[str]
    entities: list[str]
    why_it_matters: str | None
    read_minutes: int
    published_at: datetime | None
    read_at: datetime | None = None
    is_read: bool = False
    favorited_at: datetime | None = None
    is_favorite: bool = False
    topics: list[TopicTagOut] = Field(default_factory=list)


class ArticleDetailOut(ArticleOut):
    original_text: str
    body_source: str | None


class ArticleReadUpdate(BaseModel):
    read: bool


class ArticleFavoriteUpdate(BaseModel):
    favorite: bool


class ArticleCategoryUpdate(BaseModel):
    category: str | None = None


class ArticleTopicsUpdate(BaseModel):
    topics: list[str] = Field(default_factory=list)


class ArticleListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ArticleOut]


class BulkIngestResponse(BaseModel):
    newspaper_id: int
    articles_created: int
    articles_updated: int
    topic_tags: int


class NewspaperOut(BaseModel):
    id: int
    name: str
    edition_date: date | None
    article_count: int


class CategoryOut(BaseModel):
    category: str
    count: int


class TopicOut(BaseModel):
    id: int
    name: str
    article_count: int


class EntityOut(BaseModel):
    entity: str
    count: int
