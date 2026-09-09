"""Model construction, plus the two LLM steps: segmenting and summarising."""

from __future__ import annotations

import logging
from typing import TypeVar

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .config import CONFIG
from .schemas import ArticleSummary, PageSegmentation
from .topics import TopicSpec

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class MissingApiKey(RuntimeError):
    pass


def build_model(model_name: str, max_tokens: int = 16000) -> ChatAnthropic:
    if not CONFIG.api_key:
        raise MissingApiKey(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and put "
            "your key in it (get one at https://console.anthropic.com)."
        )
    return ChatAnthropic(
        model=model_name,
        max_tokens=max_tokens,
        api_key=CONFIG.api_key,
        output_config={"effort": CONFIG.effort},
        max_retries=4,
        timeout=600,
    )


SEGMENT_SYSTEM = """You are a precise newspaper layout parser.

You receive the text extracted from ONE page of a newspaper PDF, given twice:
first re-ordered column by column, then in a rendering that preserves the
visual layout. The two renderings are the same page. Use the column-ordered
one as the source of the body text and the layout one to resolve headlines,
bylines, section labels, and where one article ends and the next begins.

Your job is to recover the articles as a reader would experience them.

Rules:
- Transcribe body text verbatim. Repair the damage PDF extraction does --
  rejoin words split across lines, close up hyphenation, drop the
  [COLUMN BREAK] markers, merge lines into real paragraphs. Never paraphrase.
- A headline plus the text beneath it is one article. Text under a different
  headline is a different article, even in the same column.
- Report ONLY news and feature articles -- reported prose under a headline.
- Leave out entirely, and never report as an article: advertisements and
  advertorials; classifieds of every kind (matrimonial, situations vacant, to
  let, for sale, property, vehicles); public and legal notices (tenders,
  auction notices, change of name, court and company notices); any table or
  data listing (stock and commodity prices, exchange rates, scorecards,
  points tables, fixtures, results, weather tables, timetables, lottery and
  exam results); puzzles and light matter (crossword, sudoku, horoscope,
  comics); listings (TV, radio, cinema, events); page furniture (the
  masthead, repeating running headers and footers, page numbers, section
  dividers, "continued on page N" pointers, index boxes, subscription
  panels, credits); standalone photo captions with no story attached; and
  birth/death announcement notices. A reported obituary feature about a
  notable person IS an article -- include that.
- A very short item (one or two sentences under its own headline) is still an
  article. Include it.
- If the page has no articles at all, return an empty list. Do not invent one.
- Never merge two unrelated stories into one article, and never split one
  story into several just because it spans columns."""


def segment_page(page_prompt: str, page_number: int) -> PageSegmentation:
    """Split one page's extracted text into articles."""
    model = build_model(CONFIG.segment_model, max_tokens=32000)
    structured = model.with_structured_output(PageSegmentation)
    result = structured.invoke(
        [
            SystemMessage(content=SEGMENT_SYSTEM),
            HumanMessage(
                content=(
                    f"Recover every article on page {page_number} of this "
                    f"newspaper.\n\n{page_prompt}"
                )
            ),
        ]
    )
    if not isinstance(result, PageSegmentation):  # pragma: no cover - defensive
        return PageSegmentation(articles=[], page_kind="other")
    return result


SUMMARY_SYSTEM = """You summarise newspaper articles for a busy reader who
wants the substance without reading the paper.

Two jobs, both in one structured answer:

1. Summarise the article. Be concrete and specific: names, numbers, dates,
   and outcomes. Use only what the article says -- if it does not give a
   figure, do not supply one from your own knowledge, and never state as fact
   something the article attributes or hedges. Plain declarative prose, no
   editorialising, no "the article says".

2. Tag it against MY topics of interest, which are listed below. Match on
   what the article is genuinely about. A keyword appearing in passing is not
   a match: an article about a cricket match that mentions a bank sponsor is
   not an article about banking. Returning no topics is a perfectly good
   answer for an article that does not touch any of them.

Only ever use topic names exactly as spelled in the list. Never invent one."""


def _topics_block(specs: list[TopicSpec]) -> str:
    if not specs:
        return (
            "MY TOPICS OF INTEREST:\n(none configured -- return an empty "
            "topics list)"
        )
    lines = "\n".join(spec.as_prompt_line() for spec in specs)
    return f"MY TOPICS OF INTEREST:\n{lines}"


def summarise_article(
    headline: str,
    body_text: str,
    specs: list[TopicSpec],
    section: str | None = None,
    keyword_hint: dict[str, list[str]] | None = None,
) -> ArticleSummary:
    """Summarise one article and tag it against the topic list."""
    model = build_model(CONFIG.model, max_tokens=8000)
    structured = model.with_structured_output(ArticleSummary)

    hint = ""
    if keyword_hint:
        pairs = "; ".join(
            f"{topic}: {', '.join(words)}" for topic, words in keyword_hint.items()
        )
        hint = (
            "\n\nA literal keyword scan flagged these topics, which may or may "
            f"not be real matches -- judge for yourself: {pairs}"
        )

    article_block = "\n".join(
        part
        for part in [
            f"HEADLINE: {headline}",
            f"SECTION: {section}" if section else "",
            "",
            body_text,
        ]
        if part != ""
    )

    result = structured.invoke(
        [
            SystemMessage(content=SUMMARY_SYSTEM),
            HumanMessage(
                content=f"{_topics_block(specs)}{hint}\n\n---\n\n{article_block}"
            ),
        ]
    )
    if not isinstance(result, ArticleSummary):  # pragma: no cover - defensive
        raise RuntimeError("model did not return a structured summary")
    return result
