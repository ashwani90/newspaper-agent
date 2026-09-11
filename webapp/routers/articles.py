"""Article and newspaper API endpoints.

    POST  /api/articles/bulk        ingest one edition's worth of articles
    GET   /api/articles             filterable, paginated article list
    GET   /api/articles/{id}        one article, full original text included
    PATCH /api/articles/{id}/read   mark an article read or unread
    GET   /api/newspapers           distinct newspapers with article counts
    GET   /api/categories           distinct categories with article counts
    GET   /api/topics               distinct topics with article counts
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Article, ArticleTopic, Newspaper, Topic
from ..schemas import (
    ArticleDetailOut,
    ArticleListResponse,
    ArticleOut,
    ArticleReadUpdate,
    BulkIngestRequest,
    BulkIngestResponse,
    CategoryOut,
    NewspaperOut,
    TopicOut,
    TopicTagOut,
)

router = APIRouter(prefix="/api", tags=["articles"])


def _to_article_out(article: Article) -> ArticleOut:
    return ArticleOut(
        id=article.id,
        newspaper_id=article.newspaper_id,
        newspaper_name=article.newspaper.name if article.newspaper else "",
        edition_date=article.newspaper.edition_date if article.newspaper else None,
        page_number=article.page_number,
        headline=article.headline,
        byline=article.byline,
        section=article.section,
        category=article.category,
        summary_text=article.summary_text,
        bullets=article.bullets or [],
        entities=article.entities or [],
        why_it_matters=article.why_it_matters,
        read_minutes=article.read_minutes,
        published_at=article.published_at,
        read_at=article.read_at,
        is_read=article.read_at is not None,
        topics=[
            TopicTagOut(
                topic=link.topic.name,
                confidence=link.confidence,
                rationale=link.rationale,
            )
            for link in article.topic_links
        ],
    )


@router.post("/articles/bulk", response_model=BulkIngestResponse)
def bulk_ingest(payload: BulkIngestRequest, db: Session = Depends(get_db)):
    """Create or update a newspaper edition and its articles.

    Matched on file_hash (or newspaper name + edition_date) for the edition,
    and on (newspaper, page_number, headline) for each article -- so
    re-pushing the same edition updates articles in place rather than
    duplicating them. The CLI's 'push-web' command relies on this to be
    safely re-runnable.
    """
    np_data = payload.newspaper
    newspaper = None
    if np_data.file_hash:
        newspaper = db.scalar(
            select(Newspaper).where(Newspaper.file_hash == np_data.file_hash)
        )
    if newspaper is None:
        newspaper = db.scalar(
            select(Newspaper).where(
                Newspaper.name == np_data.name,
                Newspaper.edition_date == np_data.edition_date,
            )
        )
    if newspaper is None:
        newspaper = Newspaper(
            name=np_data.name,
            edition_date=np_data.edition_date,
            source_file=np_data.source_file,
            file_hash=np_data.file_hash,
        )
        db.add(newspaper)
        db.flush()
    else:
        # The pushed name is always the current source of truth (the PDF
        # filename) -- keep a previously-created record's name in sync
        # rather than leaving a stale guess from an earlier push.
        newspaper.name = np_data.name
        newspaper.source_file = np_data.source_file

    published_at = (
        datetime.combine(np_data.edition_date, datetime.min.time())
        if np_data.edition_date
        else datetime.utcnow()
    )

    # Cache known topics so repeated lookups don't hit the DB per-article.
    topic_cache: dict[str, Topic] = {
        t.name.casefold(): t for t in db.scalars(select(Topic)).all()
    }

    created = 0
    updated = 0
    tag_count = 0

    for item in payload.articles:
        row = db.scalar(
            select(Article).where(
                Article.newspaper_id == newspaper.id,
                Article.page_number == item.page_number,
                Article.headline == item.headline,
            )
        )
        is_new = row is None
        if row is None:
            row = Article(newspaper_id=newspaper.id)
            db.add(row)

        row.page_number = item.page_number
        row.headline = item.headline
        row.byline = item.byline
        row.section = item.section
        row.category = item.category
        row.original_text = item.original_text
        row.summary_text = item.summary_text
        row.bullets = item.bullets
        row.entities = item.entities
        row.why_it_matters = item.why_it_matters
        row.read_minutes = item.read_minutes
        row.body_source = item.body_source
        row.published_at = published_at
        db.flush()

        db.query(ArticleTopic).filter(ArticleTopic.article_id == row.id).delete()
        for tag in item.topics:
            key = tag.topic.strip().casefold()
            topic = topic_cache.get(key)
            if topic is None:
                topic = Topic(name=tag.topic.strip())
                db.add(topic)
                db.flush()
                topic_cache[key] = topic
            db.add(
                ArticleTopic(
                    article_id=row.id,
                    topic_id=topic.id,
                    confidence=tag.confidence,
                    rationale=tag.rationale,
                )
            )
            tag_count += 1

        if is_new:
            created += 1
        else:
            updated += 1

    db.commit()
    return BulkIngestResponse(
        newspaper_id=newspaper.id,
        articles_created=created,
        articles_updated=updated,
        topic_tags=tag_count,
    )


@router.get("/articles", response_model=ArticleListResponse)
def list_articles(
    newspaper: str | None = Query(
        None, description="Filter by newspaper name (partial match)"
    ),
    category: str | None = Query(None, description="Filter by category (exact)"),
    topic: str | None = Query(None, description="Filter by topic name (exact)"),
    date_from: date | None = Query(None, description="Published on or after this date"),
    date_to: date | None = Query(None, description="Published on or before this date"),
    q: str | None = Query(None, description="Search headline, summary, and body text"),
    unread_only: bool = Query(False, description="Only articles not yet marked read"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ArticleListResponse:
    stmt = select(Article).options(
        selectinload(Article.newspaper),
        selectinload(Article.topic_links).selectinload(ArticleTopic.topic),
    )
    stmt = stmt.join(Newspaper, Newspaper.id == Article.newspaper_id)

    if newspaper:
        stmt = stmt.where(Newspaper.name.ilike(f"%{newspaper}%"))
    if category:
        stmt = stmt.where(Article.category.ilike(category))
    if topic:
        stmt = (
            stmt.join(ArticleTopic, ArticleTopic.article_id == Article.id)
            .join(Topic, Topic.id == ArticleTopic.topic_id)
            .where(Topic.name.ilike(topic))
        )
    if date_from:
        stmt = stmt.where(
            Article.published_at >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        stmt = stmt.where(
            Article.published_at <= datetime.combine(date_to, datetime.max.time())
        )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Article.headline.ilike(like),
                Article.summary_text.ilike(like),
                Article.original_text.ilike(like),
            )
        )
    if unread_only:
        stmt = stmt.where(Article.read_at.is_(None))

    count_stmt = select(func.count()).select_from(stmt.with_only_columns(Article.id).subquery())
    total = db.scalar(count_stmt) or 0

    stmt = (
        stmt.order_by(Article.published_at.desc().nulls_last(), Article.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    articles = db.scalars(stmt).unique().all()

    return ArticleListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_to_article_out(a) for a in articles],
    )


@router.get("/articles/{article_id}", response_model=ArticleDetailOut)
def get_article(article_id: int, db: Session = Depends(get_db)) -> ArticleDetailOut:
    article = db.scalar(
        select(Article)
        .options(
            selectinload(Article.newspaper),
            selectinload(Article.topic_links).selectinload(ArticleTopic.topic),
        )
        .where(Article.id == article_id)
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    base = _to_article_out(article)
    return ArticleDetailOut(
        **base.model_dump(),
        original_text=article.original_text,
        body_source=article.body_source,
    )


@router.patch("/articles/{article_id}/read", response_model=ArticleOut)
def set_read_status(
    article_id: int, payload: ArticleReadUpdate, db: Session = Depends(get_db)
) -> ArticleOut:
    """Mark one article read (payload.read=true) or unread (false)."""
    article = db.scalar(
        select(Article)
        .options(
            selectinload(Article.newspaper),
            selectinload(Article.topic_links).selectinload(ArticleTopic.topic),
        )
        .where(Article.id == article_id)
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    article.read_at = datetime.utcnow() if payload.read else None
    db.commit()
    db.refresh(article)
    return _to_article_out(article)


@router.get("/newspapers", response_model=list[NewspaperOut])
def list_newspapers(db: Session = Depends(get_db)) -> list[NewspaperOut]:
    rows = db.execute(
        select(
            Newspaper.id, Newspaper.name, Newspaper.edition_date, func.count(Article.id)
        )
        .outerjoin(Article, Article.newspaper_id == Newspaper.id)
        .group_by(Newspaper.id)
        .order_by(Newspaper.edition_date.desc().nulls_last())
    ).all()
    return [
        NewspaperOut(id=r[0], name=r[1], edition_date=r[2], article_count=r[3])
        for r in rows
    ]


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryOut]:
    rows = db.execute(
        select(Article.category, func.count(Article.id))
        .where(Article.category.is_not(None))
        .group_by(Article.category)
        .order_by(func.count(Article.id).desc())
    ).all()
    return [CategoryOut(category=r[0], count=r[1]) for r in rows]


@router.get("/topics", response_model=list[TopicOut])
def list_topics(db: Session = Depends(get_db)) -> list[TopicOut]:
    rows = db.execute(
        select(Topic.id, Topic.name, func.count(ArticleTopic.id))
        .outerjoin(ArticleTopic, ArticleTopic.topic_id == Topic.id)
        .group_by(Topic.id)
        .order_by(func.count(ArticleTopic.id).desc())
    ).all()
    return [TopicOut(id=r[0], name=r[1], article_count=r[2]) for r in rows]
