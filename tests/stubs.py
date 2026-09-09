"""Stand-in implementations of the two LLM calls, for offline testing.

Importing this module has no side effects -- it does not touch the
environment or the database -- so it is safe to import from anywhere.

The fake segmenter splits a page on the section labels that the sample PDF
prints. That only works on the sample; it is a test fixture, not a fallback
for real newspapers.
"""

from __future__ import annotations

from newsagent.schemas import (
    ArticleSummary,
    ExtractedArticle,
    PageSegmentation,
    TopicTag,
)

SECTION_LABELS = (
    "FRONT PAGE",
    "BUSINESS",
    "HEALTH",
    "NATION",
    "EDUCATION",
    "WORLD",
)


def _finish(lines: list[str], section: str | None) -> ExtractedArticle:
    headline_parts: list[str] = []
    byline = None
    body_start = 0
    for index, line in enumerate(lines):
        if line.startswith("By ") or line.startswith("Dr "):
            byline = line
            body_start = index + 1
            break
        headline_parts.append(line)
        body_start = index + 1
    return ExtractedArticle(
        headline=" ".join(headline_parts) or "(untitled)",
        byline=byline,
        section=section,
        body_text=" ".join(lines[body_start:]),
        is_continuation=False,
    )


def fake_segment_page(page_prompt: str, page_number: int) -> PageSegmentation:
    """Split the column-ordered rendering of a page on its section labels."""
    body = page_prompt.split("=== PAGE", 2)[1]
    body = body.split("-- VISUAL LAYOUT")[0]
    lines = [ln.rstrip() for ln in body.splitlines()]

    articles: list[ExtractedArticle] = []
    current: list[str] | None = None
    section: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped in SECTION_LABELS:
            if current:
                articles.append(_finish(current, section))
            current = []
            section = stripped
            continue
        if current is not None and stripped and not stripped.startswith("==="):
            current.append(stripped)
    if current:
        articles.append(_finish(current, section))

    return PageSegmentation(
        articles=[a for a in articles if len(a.body_text) > 150], page_kind="news"
    )


def fake_summarise_article(
    headline, body_text, specs, section=None, keyword_hint=None
) -> ArticleSummary:
    """A deterministic stub summary.

    Topics come from the keyword hint, plus one deliberately invented topic so
    that the pipeline's validation of model-supplied topic names is exercised.
    """
    tags = [
        TopicTag(topic=name, confidence=0.9, rationale=f"keywords: {', '.join(words)}")
        for name, words in (keyword_hint or {}).items()
    ]
    tags.append(
        TopicTag(topic="Topic That Does Not Exist", confidence=0.95, rationale="bogus")
    )
    return ArticleSummary(
        one_liner=f"{headline[:80]} (stub summary).",
        bullets=["First stub bullet.", "Second stub bullet."],
        entities=["Stub Entity"],
        why_it_matters="Stub significance.",
        category=section or "General",
        read_minutes=2,
        topics=tags,
    )


def install(pipeline_module) -> None:  # noqa: ANN001
    """Swap the stubs into an already-imported newsagent.pipeline."""
    pipeline_module.segment_page = fake_segment_page
    pipeline_module.summarise_article = fake_summarise_article
