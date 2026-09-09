"""Pydantic schemas for the model's structured output.

These are the contract between the LLM and the database. Field descriptions
are part of the prompt -- the model sees them -- so they are written as
instructions, not as notes to the reader.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedArticle(BaseModel):
    """One article found on a newspaper page."""

    headline: str = Field(
        description="The article's headline, transcribed exactly as printed."
    )
    byline: str | None = Field(
        default=None,
        description=(
            "The author line if present, e.g. 'By Meera Raghavan, Economics "
            "Bureau'. Null if the article has no byline."
        ),
    )
    section: str | None = Field(
        default=None,
        description=(
            "The section or page label this article sits under, e.g. 'BUSINESS', "
            "'WORLD', 'SPORT'. Null if not shown."
        ),
    )
    body_text: str = Field(
        description=(
            "The full body text of the article, transcribed verbatim with "
            "column breaks and mid-word hyphenation repaired into clean "
            "paragraphs separated by blank lines. Do not summarise, shorten, "
            "or rewrite it here."
        )
    )
    is_continuation: bool = Field(
        default=False,
        description=(
            "True if this text continues an article that began on an earlier "
            "page (for example it starts mid-sentence, or is headed "
            "'continued from page 1'). True also if the article is cut off at "
            "the bottom of this page."
        ),
    )


class PageSegmentation(BaseModel):
    """Every article on one page."""

    articles: list[ExtractedArticle] = Field(
        default_factory=list,
        description=(
            "Every distinct news article on the page, in reading order. "
            "Exclude advertisements, classifieds, stock tables, weather "
            "tables, TV listings, crosswords, horoscopes, and standalone "
            "photo captions."
        ),
    )
    page_kind: str = Field(
        default="news",
        description=(
            "What this page mostly is: 'news', 'opinion', 'sport', "
            "'classifieds', 'advertising', 'listings', or 'other'."
        ),
    )


class TopicTag(BaseModel):
    """A topic-of-interest match for one article."""

    topic: str = Field(
        description=(
            "The topic name, copied EXACTLY as it appears in the supplied list "
            "of topics. Never invent a topic that is not on that list."
        )
    )
    confidence: float = Field(
        description=(
            "How squarely the article belongs to this topic, from 0.0 to 1.0. "
            "Use 0.9+ when the article is substantially about the topic, "
            "0.6-0.8 when the topic is a significant thread, and below 0.5 "
            "for a passing mention. Do not report matches below 0.4."
        )
    )
    rationale: str = Field(
        description="One short clause saying why this article matches the topic."
    )


class ArticleSummary(BaseModel):
    """The generated summary of a single article."""

    one_liner: str = Field(
        description=(
            "The whole article in one plain sentence of at most 30 words. "
            "Lead with what actually happened, including the key number or "
            "name. No preamble like 'This article discusses'."
        )
    )
    bullets: list[str] = Field(
        description=(
            "Two to five bullets covering the substance: what happened, the "
            "numbers, who is affected, and what happens next. Each bullet a "
            "single sentence under 25 words. Facts from the article only -- "
            "never add outside knowledge."
        )
    )
    entities: list[str] = Field(
        default_factory=list,
        description=(
            "Up to 8 people, organisations, or places central to the story, "
            "as printed."
        ),
    )
    why_it_matters: str | None = Field(
        default=None,
        description=(
            "One sentence on the significance or consequence, but ONLY if the "
            "article itself supports it. Null rather than speculation."
        ),
    )
    category: str = Field(
        description=(
            "One broad label for the story: e.g. 'Economy', 'Technology', "
            "'Politics', 'Health', 'Sport', 'Environment', 'Business', "
            "'World', 'Education', 'Culture'."
        )
    )
    read_minutes: int = Field(
        default=1, description="Rough minutes to read the original article, at least 1."
    )
    topics: list[TopicTag] = Field(
        default_factory=list,
        description=(
            "Which of MY topics of interest this article belongs to. Judge by "
            "what the article is actually about, not by whether a keyword "
            "happens to appear -- a passing mention of a company does not make "
            "an article about that industry. Empty list if none genuinely "
            "apply; that is a normal and expected outcome."
        ),
    )
