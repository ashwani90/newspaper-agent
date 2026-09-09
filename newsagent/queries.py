"""Read-side queries, shared by the CLI and by the agent's tools.

Every function here returns plain dicts and lists rather than ORM objects, so
results survive the session closing and can be handed straight to the LLM.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from .db import Article, ArticleTopic, Edition, Summary, Topic, session_scope


def _article_dict(article: Article, *, include_body: bool = False) -> dict[str, Any]:
    summary = article.summary
    tags = sorted(
        (
            {
                "topic": link.topic.name,
                "confidence": link.confidence,
                "matched_by": link.matched_by,
                "rationale": link.rationale,
            }
            for link in article.topic_links
        ),
        key=lambda t: -t["confidence"],
    )
    data: dict[str, Any] = {
        "article_id": article.id,
        "headline": article.headline,
        "byline": article.byline,
        "section": article.section,
        "page": article.page_number,
        "word_count": article.word_count,
        "source": article.edition.source_name if article.edition else None,
        "edition_date": (
            article.edition.edition_date.isoformat()
            if article.edition and article.edition.edition_date
            else None
        ),
        "topics": tags,
    }
    if summary is not None:
        data["summary"] = {
            "one_liner": summary.one_liner,
            "bullets": summary.bullets,
            "why_it_matters": summary.why_it_matters,
            "entities": summary.entities,
            "category": summary.category,
            "read_minutes": summary.read_minutes,
        }
    else:
        data["summary"] = None
    if include_body:
        data["body_text"] = article.body_text
    return data


def _loaded(stmt):  # noqa: ANN001, ANN201
    return stmt.options(
        selectinload(Article.summary),
        selectinload(Article.topic_links).selectinload(ArticleTopic.topic),
        selectinload(Article.edition),
    )


def _parse_since(since: str | None) -> date | None:
    """Accept an ISO date, or a relative window like '7d' / '2w' / '3m'."""
    if not since:
        return None
    value = since.strip().lower()
    match = re.fullmatch(r"(\d+)\s*([dwm])", value)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        days = {"d": 1, "w": 7, "m": 30}[unit] * amount
        return date.today() - timedelta(days=days)
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _apply_filters(stmt, *, topic: str | None, since: str | None, source: str | None):  # noqa: ANN001, ANN201
    if topic:
        stmt = (
            stmt.join(ArticleTopic, ArticleTopic.article_id == Article.id)
            .join(Topic, Topic.id == ArticleTopic.topic_id)
            .where(func.lower(Topic.name) == topic.strip().lower())
        )
    if since or source:
        stmt = stmt.join(Edition, Edition.id == Article.edition_id)
        cutoff = _parse_since(since)
        if cutoff is not None:
            stmt = stmt.where(Edition.edition_date >= cutoff)
        if source:
            stmt = stmt.where(Edition.source_name.ilike(f"%{source}%"))
    return stmt


def list_topics() -> list[dict[str, Any]]:
    """Every topic with how many articles are tagged with it."""
    with session_scope() as session:
        rows = session.execute(
            select(Topic, func.count(ArticleTopic.id))
            .outerjoin(ArticleTopic, ArticleTopic.topic_id == Topic.id)
            .group_by(Topic.id)
            .order_by(func.count(ArticleTopic.id).desc(), Topic.name)
        ).all()
        return [
            {
                "topic": topic.name,
                "keywords": topic.keywords,
                "active": bool(topic.active),
                "article_count": count,
            }
            for topic, count in rows
        ]


def get_article(article_id: int, *, include_body: bool = True) -> dict[str, Any] | None:
    with session_scope() as session:
        article = session.scalar(
            _loaded(select(Article)).where(Article.id == article_id)
        )
        if article is None:
            return None
        return _article_dict(article, include_body=include_body)


def articles_by_topic(
    topic: str, *, limit: int = 20, since: str | None = None
) -> list[dict[str, Any]]:
    """Summaries tagged with one topic, strongest match first."""
    with session_scope() as session:
        stmt = (
            _loaded(select(Article))
            .join(ArticleTopic, ArticleTopic.article_id == Article.id)
            .join(Topic, Topic.id == ArticleTopic.topic_id)
            .where(func.lower(Topic.name) == topic.strip().lower())
            .order_by(ArticleTopic.confidence.desc(), Article.id.desc())
            .limit(limit)
        )
        cutoff = _parse_since(since)
        if cutoff is not None:
            stmt = stmt.join(Edition, Edition.id == Article.edition_id).where(
                Edition.edition_date >= cutoff
            )
        return [_article_dict(a) for a in session.scalars(stmt).unique().all()]


_FTS_SPECIALS = re.compile(r'[":*^(){}\[\]-]')


def search_articles(
    query: str,
    *,
    limit: int = 20,
    topic: str | None = None,
    since: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Full-text search over headlines, bodies, and summaries.

    Plain words are ANDed. FTS5 operator characters in the query are stripped
    so a user's punctuation can never produce a syntax error.
    """
    cleaned = _FTS_SPECIALS.sub(" ", query or "").strip()
    if not cleaned:
        return []
    terms = [f'"{word}"' for word in cleaned.split() if word]
    match_expr = " AND ".join(terms)

    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT article_id, bm25(articles_fts, 8.0, 1.0, 4.0) AS score "
                "FROM articles_fts WHERE articles_fts MATCH :q "
                "ORDER BY score LIMIT :lim"
            ),
            {"q": match_expr, "lim": max(limit * 4, limit)},
        ).all()
        if not rows:
            return []
        ranked = {int(r[0]): float(r[1]) for r in rows}

        stmt = _apply_filters(
            _loaded(select(Article)).where(Article.id.in_(list(ranked))),
            topic=topic,
            since=since,
            source=source,
        )
        found = session.scalars(stmt).unique().all()
        found.sort(key=lambda a: ranked.get(a.id, 0.0))
        return [_article_dict(a) for a in found[:limit]]


def digest(
    *,
    since: str | None = None,
    topics_only: bool = True,
    limit: int = 60,
    source: str | None = None,
) -> dict[str, Any]:
    """A reading digest, grouped by topic.

    With topics_only=True (the default) this is the answer to "what should I
    read today" -- only articles matching a topic in topics.txt. Set it False
    to include everything else in an 'Untagged' bucket.
    """
    with session_scope() as session:
        stmt = _apply_filters(
            _loaded(select(Article)).join(Summary, Summary.article_id == Article.id),
            topic=None,
            since=since,
            source=source,
        ).order_by(Article.page_number, Article.id)
        articles = session.scalars(stmt).unique().all()

        grouped: dict[str, list[dict[str, Any]]] = {}
        untagged: list[dict[str, Any]] = []
        tagged_ids: set[int] = set()
        for article in articles:
            data = _article_dict(article)
            if data["topics"]:
                tagged_ids.add(data["article_id"])
                # An article matching several topics appears under each of
                # them, which is what you want when reading by topic.
                for tag in data["topics"]:
                    grouped.setdefault(tag["topic"], []).append(data)
            else:
                untagged.append(data)

        for items in grouped.values():
            items.sort(
                key=lambda d: -max(
                    (t["confidence"] for t in d["topics"]), default=0.0
                )
            )

        result: dict[str, Any] = {
            "window": since or "all time",
            "articles_considered": len(articles),
            # Distinct articles, so this never exceeds articles_considered.
            "matched_your_topics": len(tagged_ids),
            "topic_placements": sum(len(v) for v in grouped.values()),
            "by_topic": {
                name: items[:limit]
                for name, items in sorted(
                    grouped.items(), key=lambda kv: -len(kv[1])
                )
            },
        }
        if not topics_only:
            result["untagged"] = untagged[:limit]
            result["untagged_count"] = len(untagged)
        return result


def list_editions(limit: int = 25) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.execute(
            select(Edition, func.count(Article.id))
            .outerjoin(Article, Article.edition_id == Edition.id)
            .group_by(Edition.id)
            .order_by(Edition.ingested_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "edition_id": e.id,
                "source": e.source_name,
                "edition_date": e.edition_date.isoformat() if e.edition_date else None,
                "pages": e.page_count,
                "articles": count,
                "status": e.status,
                "note": e.note,
                "pdf": e.pdf_path,
                "ingested_at": e.ingested_at.isoformat(timespec="seconds"),
            }
            for e, count in rows
        ]


def stats() -> dict[str, Any]:
    with session_scope() as session:
        from .db import counts

        base = counts(session)
        base["untagged_articles"] = (
            session.scalar(
                select(func.count())
                .select_from(Article)
                .outerjoin(ArticleTopic, ArticleTopic.article_id == Article.id)
                .where(ArticleTopic.id.is_(None))
            )
            or 0
        )
        base["awaiting_summary"] = (
            session.scalar(
                select(func.count())
                .select_from(Article)
                .outerjoin(Summary, Summary.article_id == Article.id)
                .where(Summary.id.is_(None))
            )
            or 0
        )
        return base


def export_articles(
    *,
    since: str | None = None,
    topics_only: bool = False,
    source: str | None = None,
    include_body: bool = True,
    summarised_only: bool = True,
) -> list[dict[str, Any]]:
    """Every article as a plain dict, for the HTML report.

    Ordered newest edition first, then by page, which is the order a reader
    expects. Unlike `digest`, an article appears once no matter how many
    topics it matched -- the report filters by topic on the client side.
    """
    with session_scope() as session:
        # The edition join is built here rather than via _apply_filters,
        # because this query needs it for ordering as well as filtering and
        # joining the same table twice makes every column reference ambiguous.
        stmt = _loaded(select(Article))
        if summarised_only:
            stmt = stmt.join(Summary, Summary.article_id == Article.id)
        stmt = stmt.join(Edition, Edition.id == Article.edition_id)

        cutoff = _parse_since(since)
        if cutoff is not None:
            stmt = stmt.where(Edition.edition_date >= cutoff)
        if source:
            stmt = stmt.where(Edition.source_name.ilike(f"%{source}%"))

        stmt = stmt.order_by(
            Edition.edition_date.desc().nulls_last(),
            Edition.id.desc(),
            Article.page_number,
            Article.id,
        )
        articles = session.scalars(stmt).unique().all()

        out: list[dict[str, Any]] = []
        for article in articles:
            data = _article_dict(article, include_body=include_body)
            if topics_only and not data["topics"]:
                continue
            out.append(data)
        return out


def unsummarised_article_ids(limit: int = 500) -> list[int]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(Article.id)
                .outerjoin(Summary, Summary.article_id == Article.id)
                .where(Summary.id.is_(None))
                .order_by(Article.id)
                .limit(limit)
            ).all()
        )
