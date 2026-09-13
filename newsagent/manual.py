"""The no-API workflow: build prompts to paste into an LLM chat window, and
parse the answer back into the database.

Two halves:

    build_prompts()   page text -> numbered prompt files you paste into chat
    parse_response()  what you paste back -> structured articles

The response format is deliberately line-oriented rather than JSON. Pasting
from a chat window truncates, wraps, adds markdown fences, and bolds things;
a block format survives all of that, and a truncated paste still yields every
complete article before the cut instead of failing outright. JSON is also
accepted, since some models insist on it.

Article bodies are not echoed back by the model -- it returns a short
verbatim ANCHOR (the article's opening words) and the body is sliced out of
the page text we already hold locally.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from .topics import TopicSpec

# --------------------------------------------------------------------------- #
# Prompt building
# --------------------------------------------------------------------------- #

# Default budget per prompt file, in characters of page text. Chat windows
# take far more than this, but a smaller prompt means a shorter reply, and
# a reply that fits in one response without being truncated is the thing
# that actually matters here.
DEFAULT_CHUNK_CHARS = 60_000

INSTRUCTIONS = """\
You are reading text extracted from a newspaper PDF. Your job is to find every
article on the pages below, summarise each one, and tag it against my topics of
interest.

The text has been re-ordered column by column, so it reads top-to-bottom
down each column in turn. Headlines, bylines and section labels appear on
their own lines above the article they belong to. Some pages also include a
second rendering that preserves the visual layout; when present, use it to
resolve any headline or boundary the column view makes ambiguous.

WHAT COUNTS AS AN ARTICLE
Report ONLY news and feature articles: a headline with reported prose beneath
it, written by a journalist. Text under a different headline is a different
article, even in the same column. A short item under its own headline still
counts -- include it. If an article is continued from an earlier page or runs
off the bottom, report it and set CONTINUED: yes.

WHAT COUNTS AS A TABLE
A page sometimes carries a genuine data table instead of, or alongside,
articles -- stock and commodity prices, exchange rates, market summaries,
sports scorecards, points tables, fixtures and results, weather tables, or a
financial/statistical table embedded in a story. Do not transcribe it row by
row. Instead report ONE TABLE block per table (see OUTPUT FORMAT below) that
explains, in plain language, what the table shows and what it means -- e.g.
"the Sensex and Nifty closed higher for the third straight session, led by
banking stocks" rather than a row-by-row readout. Several small tables that
clearly belong to the same subject (e.g. today's closing levels for a handful
of indices) can share one TABLE block.

Tide tables, prayer times, lottery-number listings, and exam-result roll
numbers carry no meaning beyond the raw numbers themselves -- leave those out
entirely, same as the other listings below.

WHAT TO LEAVE OUT ENTIRELY
Skip all of the following. Do not summarise them, do not report them as
articles or tables, and do not mention them in your reply:
- Advertisements and advertorials, including full-page ads and anything
  marked "sponsored", "promotional feature" or "terms and conditions apply".
- Classifieds of every kind: matrimonial, situations vacant, to let, for
  sale, property, vehicles, lost and found.
- Public and legal notices: tenders, e-tenders, auction notices, change of
  name, court and company notices, statutory declarations.
- Puzzles and light matter: crossword, sudoku, horoscope, comics, cartoons.
- Listings: TV and radio schedules, cinema showtimes, event calendars,
  train and flight timetables, tide tables, prayer times, lottery numbers,
  exam-result roll numbers.
- Page furniture: the masthead, the running header or footer that repeats on
  every page, page numbers, section dividers, "continued on page N" pointers,
  index or contents boxes, subscription and contact panels, credits, and
  standalone photo captions with no story attached.
- Obituary and birth/death announcement notices. (An obituary written as a
  reported feature about a notable person IS an article -- include that.)

If a whole page turns out to have neither an article nor an explainable
table, reply with the single line NO ARTICLES ON THIS PAGE and nothing else.
That is a correct and useful answer -- never invent an article or a table to
fill the space. Do not write that line on a page where you are reporting one
or more TABLE blocks.

HOW TO SUMMARISE
- Be concrete: names, numbers, dates, outcomes.
- Use ONLY what the article says. Never add outside knowledge, and never state
  as fact something the article attributes or hedges.
- Plain declarative prose. No "this article discusses".

HOW TO TAG TOPICS
- Match on what the article is genuinely ABOUT, not on whether a keyword
  appears. A cricket report that mentions a bank sponsor is not about banking.
- Use topic names EXACTLY as spelled in my list. Never invent one.
- No topics at all is a normal, correct answer for many articles.

THE ANCHOR LINE
For each article, ANCHOR must be the first 10-15 words of the article's body
text, copied EXACTLY as they appear in the page text above (not the headline,
not the byline -- the first words of the actual article). This is how the tool
locates the full text, so it must be verbatim. For a TABLE block, ANCHOR is
instead 8-15 words copied verbatim from the table's own caption/title or the
text introducing it -- whatever sits closest to the table on the page.

OUTPUT FORMAT
Reply with ONLY article and table blocks in exactly this shape. No preamble,
no closing remarks, no markdown headings other than the ### markers.

### ARTICLE
PAGE: 1
HEADLINE: the headline, exactly as printed
BYLINE: the author line, or omit this line entirely
SECTION: the section label, or omit
CATEGORY: one broad label, e.g. Economy, Technology, Politics, Health, Sport
ANCHOR: the first 10-15 words of the body, verbatim
READ_MINUTES: 3
CONTINUED: no
SUMMARY: the whole article in one sentence under 30 words
BULLET: a key point in one sentence
BULLET: another key point
BULLET: another key point
BULLET: another key point (optional)
BULLET: another key point (optional)
ENTITIES: Person One, Organisation Two, Place Three
WHY: one sentence on why it matters, or omit if the article does not support one
TOPIC: Exact Topic Name | 0.95 | short reason this article matches
### END

Repeat that block for every article. Use BULLET two to five times per article.
Use TOPIC once per matching topic, and omit it entirely when nothing matches.

When the page has a data table worth explaining (see WHAT COUNTS AS A TABLE
above), ALSO report one block per table in this shape:

### TABLE
PAGE: 1
CAPTION: short label for what the table is, e.g. "Sensex and Nifty closing levels"
ANCHOR: 8-15 words copied verbatim from the table's caption or the text introducing it
EXPLANATION: 1-3 plain-language sentences on what the table shows and what it means
### END
"""


@dataclass
class PromptChunk:
    index: int
    total: int
    page_numbers: list[int]
    text: str

    @property
    def filename(self) -> str:
        return f"chunk-{self.index:02d}.txt"


def _topics_block(specs: list[TopicSpec]) -> str:
    if not specs:
        return (
            "MY TOPICS OF INTEREST\n"
            "(none configured -- omit every TOPIC line)"
        )
    lines = "\n".join(spec.as_prompt_line() for spec in specs)
    return f"MY TOPICS OF INTEREST\n{lines}"


def build_prompts(
    pages: list[tuple[int, str]],
    specs: list[TopicSpec],
    *,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    pages_per_chunk: int | None = None,
) -> list[PromptChunk]:
    """Pack page renderings into paste-sized prompt files.

    ``pages`` is a list of (page_number, rendering). Packing is by character
    budget by default, so a sparse paper gets more pages per prompt than a
    dense one; pass pages_per_chunk to force a fixed count.
    """
    if not pages:
        return []

    groups: list[list[tuple[int, str]]] = []
    if pages_per_chunk and pages_per_chunk > 0:
        for start in range(0, len(pages), pages_per_chunk):
            groups.append(pages[start : start + pages_per_chunk])
    else:
        current: list[tuple[int, str]] = []
        size = 0
        for page_no, rendering in pages:
            # A single page over budget still gets its own chunk rather than
            # being split mid-article.
            if current and size + len(rendering) > max_chars:
                groups.append(current)
                current, size = [], 0
            current.append((page_no, rendering))
            size += len(rendering)
        if current:
            groups.append(current)

    total = len(groups)
    chunks: list[PromptChunk] = []
    for index, group in enumerate(groups, start=1):
        page_numbers = [p for p, _ in group]
        span = (
            f"page {page_numbers[0]}"
            if len(page_numbers) == 1
            else f"pages {page_numbers[0]}-{page_numbers[-1]}"
        )
        header = (
            f"[Prompt {index} of {total} -- {span}. Each prompt is independent; "
            f"paste them one at a time and save each reply.]"
        )
        body = "\n\n".join(rendering for _, rendering in group)
        chunks.append(
            PromptChunk(
                index=index,
                total=total,
                page_numbers=page_numbers,
                text=(
                    f"{header}\n\n{INSTRUCTIONS}\n\n{_topics_block(specs)}\n\n"
                    f"{'=' * 70}\nNEWSPAPER TEXT ({span})\n{'=' * 70}\n\n{body}\n"
                ),
            )
        )
    return chunks


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #


@dataclass
class ParsedArticle:
    page: int | None = None
    headline: str = ""
    byline: str | None = None
    section: str | None = None
    category: str | None = None
    anchor: str = ""
    read_minutes: int = 1
    continued: bool = False
    summary: str = ""
    bullets: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    why: str | None = None
    topics: list[tuple[str, float, str]] = field(default_factory=list)
    is_table: bool = False

    def is_usable(self) -> bool:
        return bool(self.headline.strip() and self.summary.strip())


@dataclass
class ParseResult:
    articles: list[ParsedArticle] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    format_seen: str = "blocks"


# Strips markdown emphasis and list markers a chat window may have added:
#   "**HEADLINE:** foo"  ->  "HEADLINE: foo"
#   "- BULLET: foo"      ->  "BULLET: foo"
_LEADING_JUNK = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?[*_`]*\s*")
_KEY_LINE = re.compile(
    r"^(PAGE|HEADLINE|CAPTION|BYLINE|SECTION|CATEGORY|ANCHOR|READ_MINUTES|READ MINUTES"
    r"|CONTINUED|SUMMARY|EXPLANATION|BULLET|BULLETS|ENTITIES|WHY|WHY_IT_MATTERS"
    r"|WHY IT MATTERS|TOPIC|TOPICS)\s*[:\-]\s*(.*)$",
    re.IGNORECASE,
)
_BLOCK_START = re.compile(r"^\s*#{2,4}\s*(ARTICLE|TABLE)\b", re.IGNORECASE)
_BLOCK_END = re.compile(r"^\s*#{2,4}\s*END\b", re.IGNORECASE)
_FENCE = re.compile(r"^\s*```")

# The prompt tells the model to answer with "NO ARTICLES ON THIS PAGE" when a
# page holds nothing but adverts, classifieds, listings or an unexplainable
# table. Matched loosely, because models paraphrase the sentinel rather than
# echoing it exactly.
_NO_ARTICLES = re.compile(
    r"\bno\s+(?:news\s+|real\s+|actual\s+)?articles?\b"
    r"[^.\n]{0,40}?"
    r"\b(?:on|in|found|present|here|were|was|to\s+report)\b",
    re.IGNORECASE,
)


def _clean_value(value: str) -> str:
    """Drop trailing markdown emphasis and surrounding quotes."""
    text = value.strip()
    text = re.sub(r"[*_`]+$", "", text).strip()
    text = re.sub(r"^[*_`]+", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _split_list(value: str) -> list[str]:
    parts = [p.strip(" .;") for p in re.split(r"[,;]", value)]
    return [p for p in parts if p]


def _parse_topic_line(value: str) -> tuple[str, float, str] | None:
    """Parse 'Topic Name | 0.9 | reason', tolerating missing trailing fields."""
    pieces = [p.strip() for p in value.split("|")]
    name = _clean_value(pieces[0]) if pieces else ""
    if not name:
        return None
    confidence = 0.8
    if len(pieces) > 1 and pieces[1]:
        match = re.search(r"(\d*\.?\d+)\s*%?", pieces[1])
        if match:
            try:
                confidence = float(match.group(1))
                if confidence > 1.0:  # given as a percentage
                    confidence /= 100.0
            except ValueError:
                pass
    rationale = _clean_value(pieces[2]) if len(pieces) > 2 else ""
    return name, max(0.0, min(1.0, confidence)), rationale


def _finalise(article: ParsedArticle) -> ParsedArticle:
    article.headline = _clean_value(article.headline)
    article.summary = _clean_value(article.summary)
    article.read_minutes = max(1, article.read_minutes)
    return article


def parse_response(raw: str) -> ParseResult:
    """Parse a pasted chat reply into articles.

    Handles the documented block format, and falls back to JSON if the model
    returned that instead. Unknown lines are ignored rather than fatal, and a
    reply truncated mid-article keeps every complete article before the cut.
    """
    result = ParseResult()
    text = unicodedata.normalize("NFC", raw or "").replace("\r\n", "\n")
    if not text.strip():
        result.warnings.append("the response was empty")
        return result

    # The prompt asks for this when a page is all adverts, classifieds and
    # listings with no explainable table either. It is a correct answer, so
    # it must not be treated as a failure -- but not when a TABLE block is
    # also present, since that means the page did have something to report.
    upper = text.upper()
    if _NO_ARTICLES.search(text) and "### ARTICLE" not in upper and "### TABLE" not in upper:
        result.format_seen = "no-articles"
        return result

    stripped = text.strip()
    looks_like_json = stripped.startswith(("{", "[")) or bool(
        re.search(r"```\s*json", stripped, re.IGNORECASE)
    )
    if looks_like_json and "### ARTICLE" not in stripped.upper():
        parsed = _parse_json_response(stripped, result)
        if parsed:
            result.format_seen = "json"
            return result

    current: ParsedArticle | None = None
    saw_any_marker = False

    for line in text.split("\n"):
        if _FENCE.match(line):
            continue
        block_start = _BLOCK_START.match(line)
        if block_start:
            saw_any_marker = True
            if current is not None and current.is_usable():
                result.articles.append(_finalise(current))
            current = ParsedArticle()
            if block_start.group(1).upper() == "TABLE":
                current.is_table = True
                current.category = "Table"
            continue
        if _BLOCK_END.match(line):
            if current is not None and current.is_usable():
                result.articles.append(_finalise(current))
            current = None
            continue

        candidate = _LEADING_JUNK.sub("", line)
        match = _KEY_LINE.match(candidate)
        if match is None:
            # A continuation line of a multi-line SUMMARY or WHY value.
            if current is not None and line.strip() and current.summary:
                current.summary = f"{current.summary} {line.strip()}"
            continue

        if current is None:
            # Fields arrived without a ### ARTICLE marker; start one anyway.
            current = ParsedArticle()

        key = match.group(1).upper().replace(" ", "_")
        value = _clean_value(match.group(2))
        if not value and key not in {"CONTINUED"}:
            continue

        if key == "PAGE":
            digits = re.search(r"\d+", value)
            if digits:
                current.page = int(digits.group())
        elif key in {"HEADLINE", "CAPTION"}:
            current.headline = value
        elif key == "BYLINE":
            current.byline = value or None
        elif key == "SECTION":
            current.section = value or None
        elif key == "CATEGORY":
            current.category = value or None
        elif key == "ANCHOR":
            current.anchor = value
        elif key in {"READ_MINUTES"}:
            digits = re.search(r"\d+", value)
            if digits:
                current.read_minutes = int(digits.group())
        elif key == "CONTINUED":
            current.continued = value.strip().lower() in {"yes", "true", "y", "1"}
        elif key in {"SUMMARY", "EXPLANATION"}:
            current.summary = value
        elif key in {"BULLET", "BULLETS"}:
            if key == "BULLETS":
                current.bullets.extend(_split_list(value))
            else:
                current.bullets.append(value)
        elif key == "ENTITIES":
            current.entities.extend(_split_list(value))
        elif key in {"WHY", "WHY_IT_MATTERS"}:
            current.why = value or None
        elif key in {"TOPIC", "TOPICS"}:
            for piece in value.split("\n"):
                tag = _parse_topic_line(piece)
                if tag:
                    current.topics.append(tag)

    if current is not None and current.is_usable():
        result.articles.append(_finalise(current))

    if not result.articles:
        if saw_any_marker:
            result.warnings.append(
                "found ### ARTICLE/TABLE markers but none had both a "
                "HEADLINE/CAPTION and a SUMMARY/EXPLANATION line"
            )
        else:
            result.warnings.append(
                "no article or table blocks found -- expected lines like "
                "'### ARTICLE' then 'HEADLINE:' and 'SUMMARY:' (or '### TABLE' "
                "then 'CAPTION:' and 'EXPLANATION:'). Check you pasted the "
                "whole reply, and that the model followed the OUTPUT FORMAT "
                "section"
            )

    for article in result.articles:
        if not article.anchor:
            result.warnings.append(
                f"no ANCHOR for {article.headline[:60]!r} -- its full text "
                f"cannot be located in the page"
            )
        if not article.bullets and not article.is_table:
            result.warnings.append(
                f"no BULLET lines for {article.headline[:60]!r}"
            )
    return result


def _parse_json_response(text: str, result: ParseResult) -> bool:
    """Best-effort JSON path, for models that ignore the block format."""
    body = text
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        body = fence.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        result.warnings.append(
            f"looked like JSON but did not parse ({exc.msg} at line "
            f"{exc.lineno}) -- if the reply was cut off, ask the model to "
            f"continue, or re-run with the block format"
        )
        return False

    items = data.get("articles", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        result.warnings.append("JSON did not contain a list of articles")
        return False

    def pick(source: dict, *names: str, default=None):  # noqa: ANN001, ANN202
        for name in names:
            if name in source and source[name] not in (None, ""):
                return source[name]
        return default

    for item in items:
        if not isinstance(item, dict):
            continue
        article = ParsedArticle(
            page=_as_int(pick(item, "page", "page_number")),
            headline=str(pick(item, "headline", "title", default="")),
            byline=_as_str_or_none(pick(item, "byline", "author")),
            section=_as_str_or_none(pick(item, "section")),
            category=_as_str_or_none(pick(item, "category")),
            anchor=str(pick(item, "anchor", "first_words", default="")),
            read_minutes=_as_int(pick(item, "read_minutes", "readMinutes")) or 1,
            continued=bool(pick(item, "continued", "is_continuation", default=False)),
            summary=str(pick(item, "summary", "one_liner", "oneLiner", default="")),
        )
        bullets = pick(item, "bullets", "key_points", default=[])
        if isinstance(bullets, list):
            article.bullets = [str(b).strip() for b in bullets if str(b).strip()]
        elif isinstance(bullets, str):
            article.bullets = _split_list(bullets)

        entities = pick(item, "entities", default=[])
        if isinstance(entities, list):
            article.entities = [str(e).strip() for e in entities if str(e).strip()]
        elif isinstance(entities, str):
            article.entities = _split_list(entities)

        article.why = _as_str_or_none(pick(item, "why", "why_it_matters"))

        for tag in pick(item, "topics", default=[]) or []:
            if isinstance(tag, str):
                article.topics.append((tag.strip(), 0.8, ""))
            elif isinstance(tag, dict):
                name = str(pick(tag, "topic", "name", default="")).strip()
                if not name:
                    continue
                raw_conf = pick(tag, "confidence", "score", default=0.8)
                try:
                    conf = float(raw_conf)
                except (TypeError, ValueError):
                    conf = 0.8
                if conf > 1.0:
                    conf /= 100.0
                article.topics.append(
                    (
                        name,
                        max(0.0, min(1.0, conf)),
                        str(pick(tag, "rationale", "reason", default="")),
                    )
                )

        if article.is_usable():
            result.articles.append(_finalise(article))

    if not result.articles:
        result.warnings.append("JSON parsed but contained no usable articles")
        return False
    return True


def _as_int(value) -> int | None:  # noqa: ANN001
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str_or_none(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# --------------------------------------------------------------------------- #
# Anchoring article bodies back into the page text
# --------------------------------------------------------------------------- #

_WS = re.compile(r"\s+")


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Casefold and collapse whitespace, keeping a map back to original offsets.

    Needed because the model's ANCHOR will differ from the page text in
    whitespace and case even when it is a faithful transcription -- PDF
    extraction breaks lines mid-phrase.
    """
    out: list[str] = []
    index_map: list[int] = []
    previous_was_space = True
    for position, char in enumerate(text):
        if char.isspace():
            if not previous_was_space:
                out.append(" ")
                index_map.append(position)
                previous_was_space = True
            continue
        out.append(char.casefold())
        index_map.append(position)
        previous_was_space = False
    return "".join(out), index_map


def _find_anchor(page_norm: str, anchor: str, start_at: int = 0) -> int | None:
    """Locate an anchor in normalised page text, shortening it if need be."""
    anchor_norm = _WS.sub(" ", anchor.strip()).casefold()
    if not anchor_norm:
        return None

    found = page_norm.find(anchor_norm, start_at)
    if found != -1:
        return found

    # The model may have paraphrased the tail or dropped a word. Retry with
    # progressively shorter prefixes, down to four words.
    words = anchor_norm.split()
    for length in range(len(words) - 1, 3, -1):
        prefix = " ".join(words[:length])
        found = page_norm.find(prefix, start_at)
        if found != -1:
            return found

    # Last resort: search from the top of the page, in case the articles came
    # back out of order.
    if start_at > 0:
        return _find_anchor(page_norm, anchor, 0)
    return None


def _find_in_window(
    page_norm: str, needle: str, start: int, stop: int, *, min_words: int = 3
) -> int | None:
    """Find a phrase between two offsets, shortening it if it does not match.

    Used to locate where the *next* article's furniture begins, so a body
    does not run on into it.
    """
    phrase = _WS.sub(" ", (needle or "").strip()).casefold()
    if not phrase or start >= stop:
        return None
    window = page_norm[start:stop]

    found = window.find(phrase)
    if found != -1:
        return start + found

    words = phrase.split()
    for length in range(len(words) - 1, min_words - 1, -1):
        found = window.find(" ".join(words[:length]))
        if found != -1:
            return start + found
    return None


def slice_bodies(
    page_text: str, articles: list[ParsedArticle]
) -> list[tuple[ParsedArticle, str, str]]:
    """Slice each article's body out of the page text using its anchor.

    Returns (article, body_text, source) where source is 'anchor' when the
    body was located and 'unmatched' when it was not.

    A body starts at its own anchor and stops where the *next* article
    begins. That end point is not the next anchor: between two bodies sit the
    next article's section label, headline and byline, which belong to neither
    body. So the next article's section label and headline are located inside
    the gap and the earliest of them wins, falling back to the next anchor
    when neither can be found.
    """
    page_norm, index_map = _normalise_with_map(page_text)

    positions: list[int | None] = []
    cursor = 0
    for article in articles:
        found = (
            _find_anchor(page_norm, article.anchor, cursor) if article.anchor else None
        )
        positions.append(found)
        if found is not None:
            cursor = found + 1

    # Order by where the anchors actually sit on the page, not by the order
    # the model happened to list them. A reply that reports articles out of
    # order still gets correct boundaries this way.
    located = sorted(
        (position, index)
        for index, position in enumerate(positions)
        if position is not None
    )
    next_after: dict[int, tuple[int, int | None]] = {}
    for rank, (_, index) in enumerate(located):
        if rank + 1 < len(located):
            next_after[index] = located[rank + 1]
        else:
            next_after[index] = (len(page_norm), None)

    results: list[tuple[ParsedArticle, str, str]] = []

    for index, (article, start) in enumerate(zip(articles, positions)):
        if start is None:
            results.append((article, "", "unmatched"))
            continue

        hard_end, next_index = next_after[index]

        # Between two bodies sit the next article's section label, headline
        # and byline. Cut at the earliest of those rather than at its anchor.
        end_norm = hard_end
        if next_index is not None:
            next_article = articles[next_index]
            for candidate_text in (next_article.section, next_article.headline):
                if not candidate_text:
                    continue
                hit = _find_in_window(page_norm, candidate_text, start + 1, hard_end)
                if hit is not None and hit < end_norm:
                    end_norm = hit

        start_raw = index_map[start] if start < len(index_map) else 0
        end_raw = (
            index_map[end_norm] if end_norm < len(index_map) else len(page_text)
        )
        body = page_text[start_raw:end_raw].strip()
        results.append((article, body, "anchor" if body else "unmatched"))

    return results
