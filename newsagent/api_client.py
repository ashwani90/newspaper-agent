"""HTTP client for pushing loaded articles into the webapp's Postgres store.

The extraction/manual-chat workflow is unchanged -- it still stages an
edition's page text in the local SQLite database so prompts can be built and
article bodies sliced out by anchor. This module is the one new piece: it
takes what 'newsagent load' already parsed and stored locally, and POSTs it
to the FastAPI backend (webapp/), which is the actual source of truth the
browser UI reads from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

from .config import CONFIG


class WebPushError(Exception):
    """Raised when the webapp API is unreachable or rejects the payload."""


@dataclass
class PushReport:
    newspaper_id: int | None = None
    articles_created: int = 0
    articles_updated: int = 0
    topic_tags: int = 0
    errors: list[str] = field(default_factory=list)

    def as_lines(self) -> list[str]:
        if self.errors:
            return [f"  ERROR  {err}" for err in self.errors]
        return [
            f"  newspaper id     {self.newspaper_id}",
            f"  articles created {self.articles_created}",
            f"  articles updated {self.articles_updated}",
            f"  topic tags       {self.topic_tags}",
        ]


def push_articles(
    *,
    newspaper_name: str,
    edition_date: str | None,
    source_file: str | None,
    file_hash: str | None,
    articles: list[dict],
    api_url: str | None = None,
    timeout: float = 30.0,
) -> PushReport:
    """POST one edition's articles to the webapp's bulk-ingest endpoint.

    ``articles`` are plain dicts shaped like queries.export_articles() output
    (headline, byline, section, page, body_text, summary{...}, topics[...]).
    """
    base_url = (api_url or CONFIG.webapp_api_url or "").rstrip("/")
    if not base_url:
        return PushReport(errors=["no webapp API URL configured (NEWSAGENT_API_URL)"])

    payload = {
        "newspaper": {
            "name": newspaper_name,
            "edition_date": edition_date,
            "source_file": source_file,
            "file_hash": file_hash,
        },
        "articles": [_article_to_payload(a) for a in articles],
    }

    try:
        resp = requests.post(f"{base_url}/api/articles/bulk", json=payload, timeout=timeout)
    except requests.RequestException as exc:
        return PushReport(errors=[f"could not reach webapp at {base_url}: {exc}"])

    if resp.status_code >= 400:
        return PushReport(
            errors=[f"webapp rejected the push ({resp.status_code}): {resp.text[:500]}"]
        )

    data = resp.json()
    return PushReport(
        newspaper_id=data.get("newspaper_id"),
        articles_created=data.get("articles_created", 0),
        articles_updated=data.get("articles_updated", 0),
        topic_tags=data.get("topic_tags", 0),
    )


def _article_to_payload(article: dict) -> dict:
    summary = article.get("summary") or {}
    return {
        "page_number": article.get("page"),
        "headline": article.get("headline", "(untitled)"),
        "byline": article.get("byline"),
        "section": article.get("section"),
        "category": summary.get("category"),
        "original_text": article.get("body_text", ""),
        "summary_text": summary.get("one_liner"),
        "bullets": summary.get("bullets", []),
        "entities": summary.get("entities", []),
        "why_it_matters": summary.get("why_it_matters"),
        "read_minutes": summary.get("read_minutes", 1),
        "body_source": article.get("body_source"),
        "topics": [
            {
                "topic": t["topic"],
                "confidence": t.get("confidence", 0.0),
                "rationale": t.get("rationale"),
            }
            for t in article.get("topics", [])
        ],
    }
