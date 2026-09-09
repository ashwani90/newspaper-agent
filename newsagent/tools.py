"""The agent's tools: everything it can do to your news library.

Each tool returns JSON-serialisable data. Docstrings are the tool
descriptions the model reads, so they say what the tool is for and when to
reach for it, not how it is implemented.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from . import queries
from .config import CONFIG
from .pipeline import ingest_inbox
from .topics import parse_topics_file


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@tool
def list_my_topics() -> str:
    """List the reader's configured topics of interest and how many stored
    articles are tagged with each.

    Use this first when the reader asks about "my topics", "my interests", or
    when you need the exact spelling of a topic name for another tool."""
    return _json(
        {
            "topics_file": str(CONFIG.topics_file),
            "topics": queries.list_topics(),
        }
    )


@tool
def get_digest(since: str = "7d", topics_only: bool = True) -> str:
    """Get a reading digest of summarised articles grouped by topic of interest.

    This is the right tool for open requests like "what should I read", "catch
    me up", "what's new in my topics", or "brief me on this week".

    Args:
        since: Time window. A relative window like '1d', '7d', '2w', '3m', or
            an ISO date like '2026-03-01'. Use 'all' for no limit.
        topics_only: True to include only articles matching the reader's
            topics. Set False when they explicitly want everything.
    """
    window = None if since.strip().lower() in {"all", "", "any"} else since
    return _json(queries.digest(since=window, topics_only=topics_only))


@tool
def search_news(
    query: str,
    topic: str = "",
    since: str = "",
    limit: int = 15,
) -> str:
    """Full-text search the stored articles by keyword or phrase.

    Searches headlines, article bodies, and the generated summaries. Use this
    when the reader names something specific -- a company, a person, a place,
    a scheme, an event.

    Args:
        query: Words or a phrase to look for, e.g. 'repo rate' or 'dengue vaccine'.
        topic: Optional topic name to restrict results to. Must match a name
            from list_my_topics exactly.
        since: Optional time window, e.g. '7d', '1m', or an ISO date.
        limit: Maximum results to return.
    """
    return _json(
        queries.search_articles(
            query,
            limit=limit,
            topic=topic or None,
            since=since or None,
        )
    )


@tool
def articles_for_topic(topic: str, since: str = "", limit: int = 20) -> str:
    """Get the summarised articles tagged with one specific topic of interest.

    Args:
        topic: The topic name, spelled exactly as list_my_topics returns it.
        since: Optional time window, e.g. '7d' or '2026-03-01'.
        limit: Maximum articles to return.
    """
    return _json(
        queries.articles_by_topic(topic, limit=limit, since=since or None)
    )


@tool
def read_full_article(article_id: int) -> str:
    """Fetch the complete original text of one stored article.

    Use this only when the summary is not enough -- for example the reader
    asks for detail, an exact quote, or a specific figure the summary omits.
    Article ids come from the other tools.

    Args:
        article_id: The article's id.
    """
    article = queries.get_article(article_id, include_body=True)
    if article is None:
        return _json({"error": f"no article with id {article_id}"})
    return _json(article)


@tool
def list_editions() -> str:
    """List the newspaper editions (PDFs) that have been ingested, newest first.

    Use this to answer "which papers do you have", to check whether a
    particular day is loaded, or to diagnose an edition that failed to
    process."""
    return _json(queries.list_editions())


@tool
def library_stats() -> str:
    """Report how much is in the library: editions, articles, summaries, tags,
    and how many articles are still awaiting a summary."""
    return _json(queries.stats())


@tool
def ingest_inbox_pdfs(force: bool = False) -> str:
    """Process any newspaper PDFs sitting in the inbox folder: parse them,
    extract the articles, summarise each one, and tag them against the
    reader's topics.

    This costs API tokens and can take several minutes for a full edition, so
    only call it when the reader actually asks to process, ingest, or read in
    new papers.

    Args:
        force: True to re-process an edition that was already ingested.
    """
    reports = ingest_inbox(force=force)
    if not reports:
        return _json(
            {
                "inbox": str(CONFIG.inbox),
                "message": "No PDFs found in the inbox folder.",
            }
        )
    return _json(
        [
            {
                "pdf": r.pdf.name,
                "edition_id": r.edition_id,
                "skipped": r.skipped_reason,
                "pages_with_text": r.pages_with_text,
                "pages_needing_ocr": r.image_only_pages,
                "articles": r.articles_found,
                "summaries": r.summaries_written,
                "topic_tags": r.topic_tags,
                "errors": r.errors,
            }
            for r in reports
        ]
    )


@tool
def show_topics_file() -> str:
    """Show the raw contents of the reader's topics.txt file, and its path.

    Use this when they ask how their topics are configured, or want help
    editing the file. You cannot write to this file -- it is theirs to edit --
    so when they want a change, show them the exact line to add or remove."""
    path = CONFIG.topics_file
    return _json(
        {
            "path": str(path),
            "exists": path.exists(),
            "contents": path.read_text(encoding="utf-8") if path.exists() else "",
            "parsed_topic_count": len(parse_topics_file()),
        }
    )


ALL_TOOLS = [
    list_my_topics,
    get_digest,
    search_news,
    articles_for_topic,
    read_full_article,
    list_editions,
    library_stats,
    ingest_inbox_pdfs,
    show_topics_file,
]
