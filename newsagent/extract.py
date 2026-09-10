"""Turn a newspaper PDF into per-page text.

Newspaper pages are multi-column, so plain text extraction interleaves
columns into nonsense. Two things fix that here:

1. pdfplumber's ``layout=True`` mode, which preserves the visual position of
   text, so columns stay visually separated in the output string.
2. A column-splitting pass that detects the vertical gutters on a page and
   reads each column top-to-bottom, in order.

Both renderings are handed to the segmentation model. Giving it the layout
view plus the column-ordered view is markedly more reliable than either
alone, because when one rendering garbles a headline the other usually
does not.

If a page yields no text at all, it is a scanned image and needs OCR --
this module reports that rather than silently returning empty articles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pdfplumber


@dataclass
class PageText:
    page_number: int
    layout_text: str
    column_text: str
    char_count: int
    has_text_layer: bool

    def for_prompt(self, *, include_layout: bool = True) -> str:
        """The rendering handed to the model.

        The layout view is typically 5x larger than the column view because
        ``layout=True`` pads with spaces to preserve position. That is cheap
        over the API but wasteful when a human is pasting the prompt into a
        chat window, so the manual workflow leaves it out by default.
        """
        head = (
            f"=== PAGE {self.page_number} -- COLUMN-ORDERED READING ===\n"
            f"{self.column_text}"
        )
        if not include_layout:
            return head
        return (
            f"{head}\n\n"
            f"=== PAGE {self.page_number} -- VISUAL LAYOUT (for headline/caption cues) ===\n"
            f"{self.layout_text}"
        )


@dataclass
class PdfDocument:
    path: Path
    page_count: int
    pages: list[PageText]
    source_name: str | None
    edition_date: date | None
    selected: set[int] | None = None
    skipped_by_selection: list[int] = field(default_factory=list)
    skipped_as_junk: list[int] = field(default_factory=list)

    @property
    def text_pages(self) -> list[PageText]:
        return [p for p in self.pages if p.has_text_layer]

    @property
    def image_only_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if not p.has_text_layer]


# --------------------------------------------------------------------------- #
# Page selection
# --------------------------------------------------------------------------- #


class PageSpecError(ValueError):
    pass


def parse_page_spec(spec: str, page_count: int | None = None) -> set[int]:
    """Parse a page selector like "1-4,7,10-12" into a set of page numbers.

    Accepted forms, comma- or space-separated:
        5          a single page
        1-4        an inclusive range
        -6         from the first page to 6
        8-         from 8 to the last page (needs page_count)
        odd/even   every odd or even page (needs page_count)

    Raises PageSpecError on anything it cannot make sense of, rather than
    silently processing the wrong pages.
    """
    if not spec or not spec.strip():
        return set()

    pages: set[int] = set()
    for raw in re.split(r"[,\s]+", spec.strip()):
        token = raw.strip().lower()
        if not token:
            continue

        if token in {"odd", "even"}:
            if not page_count:
                raise PageSpecError(
                    f"'{token}' needs to know how many pages the PDF has"
                )
            want_odd = token == "odd"
            pages.update(
                n for n in range(1, page_count + 1) if (n % 2 == 1) is want_odd
            )
            continue

        if token in {"all", "*"}:
            if not page_count:
                raise PageSpecError("'all' needs to know how many pages the PDF has")
            pages.update(range(1, page_count + 1))
            continue

        match = re.fullmatch(r"(\d*)\s*-\s*(\d*)", token)
        if match and (match.group(1) or match.group(2)):
            start_text, end_text = match.groups()
            start = int(start_text) if start_text else 1
            if end_text:
                end = int(end_text)
            elif page_count:
                end = page_count
            else:
                raise PageSpecError(
                    f"open-ended range '{raw}' needs to know the page count"
                )
            if start < 1 or end < 1:
                raise PageSpecError(f"page numbers start at 1, got '{raw}'")
            if start > end:
                raise PageSpecError(f"range '{raw}' runs backwards")
            pages.update(range(start, end + 1))
            continue

        if token.isdigit():
            number = int(token)
            if number < 1:
                raise PageSpecError(f"page numbers start at 1, got '{raw}'")
            pages.add(number)
            continue

        raise PageSpecError(
            f"could not understand '{raw}' -- use forms like 1-4, 7, 10-12, "
            f"8-, odd, even"
        )

    if page_count:
        out_of_range = sorted(n for n in pages if n > page_count)
        if out_of_range:
            preview = ", ".join(str(n) for n in out_of_range[:6])
            raise PageSpecError(
                f"this PDF has {page_count} pages, so page {preview} "
                f"{'do' if len(out_of_range) > 1 else 'does'} not exist"
            )
    return pages


# --------------------------------------------------------------------------- #
# Telling article pages from advertising, classifieds and listings
# --------------------------------------------------------------------------- #

# Words that dominate a page of notices, listings or market data rather than
# reporting. Matched case-insensitively as whole words.
_JUNK_MARKERS = (
    "classified", "classifieds", "tender", "tenders", "public notice",
    "situation vacant", "situations vacant", "matrimonial", "to let",
    "for sale", "obituary", "obituaries", "horoscope", "sudoku", "crossword",
    "tv listings", "cinema", "showtimes", "advertisement", "advertorial",
    "sponsored", "terms and conditions", "e-tender", "auction notice",
    "change of name", "lost and found",
)

_TABLE_MARKERS = (
    "sensex", "nifty", "bse", "nse", "scrip", "high low close", "prev close",
    "52-week", "market watch", "gainers", "losers", "exchange rate",
    "weather", "temperature", "max min", "sunrise", "sunset", "tide",
    "scorecard", "points table", "results table", "fixtures",
)


@dataclass
class PageVerdict:
    page_number: int
    kind: str          # 'articles' | 'sparse' | 'tabular' | 'notices' | 'no-text'
    confidence: float
    reason: str
    char_count: int
    word_count: int
    digit_ratio: float
    short_line_ratio: float
    sentence_count: int
    preview: str

    @property
    def looks_like_articles(self) -> bool:
        return self.kind == "articles"


def _count_markers(lowered: str, markers: tuple[str, ...]) -> int:
    hits = 0
    for marker in markers:
        pattern = r"(?<!\w)" + re.escape(marker).replace(r"\ ", r"\s+") + r"(?!\w)"
        if re.search(pattern, lowered):
            hits += 1
    return hits


def classify_page(page: PageText) -> PageVerdict:
    """Guess whether a page carries articles or is advertising/listings/tables.

    A heuristic, and deliberately cautious: it only calls a page non-article
    when the evidence is strong, because wrongly dropping a news page is far
    worse than wasting a little prompt space on an advert.
    """
    text = page.column_text or page.layout_text or ""
    stripped = text.strip()
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    body_lines = [ln for ln in lines if ln != "[COLUMN BREAK]"]

    # Prefer a line that reads like a headline over the running header, which
    # is usually "THE PAPER | 12 March 2026" and identical on every page.
    def _headline_ish(line: str) -> bool:
        if not (12 <= len(line) <= 90) or line.isdigit():
            return False
        if "|" in line and re.search(r"\d{4}", line):
            return False
        return True

    preview = next(
        (ln for ln in body_lines if _headline_ish(ln)),
        next(
            (ln for ln in body_lines if 12 <= len(ln) <= 90),
            (body_lines[0][:80] if body_lines else ""),
        ),
    )

    if not page.has_text_layer:
        return PageVerdict(
            page.page_number, "no-text", 1.0,
            "no extractable text -- scanned image, needs OCR",
            len(stripped), len(stripped.split()), 0.0, 0.0, 0, preview,
        )

    non_space = [c for c in stripped if not c.isspace()]
    digits = sum(1 for c in non_space if c.isdigit())
    digit_ratio = digits / len(non_space) if non_space else 0.0
    short_lines = sum(1 for ln in body_lines if len(ln) < 25)
    short_line_ratio = short_lines / len(body_lines) if body_lines else 0.0
    sentences = len(re.findall(r"[a-z]{2}[.!?](?:\s|$)", stripped))
    words = len(stripped.split())
    lowered = stripped.lower()

    junk_hits = _count_markers(lowered, _JUNK_MARKERS)
    table_hits = _count_markers(lowered, _TABLE_MARKERS)

    # Sentences per 100 words. This is the sharpest signal available: running
    # prose lands around 3-5, while classifieds, listings and tables sit
    # under 1 because they are fragments, not sentences. Short-line ratio is
    # much weaker -- narrow newspaper columns produce short lines too.
    density = sentences / (words / 100) if words else 0.0

    def verdict(kind: str, confidence: float, reason: str) -> PageVerdict:
        return PageVerdict(
            page.page_number, kind, confidence, reason,
            len(stripped), words, digit_ratio, short_line_ratio,
            sentences, preview,
        )

    # A whole page with under 120 words has no article on it, whatever else
    # is true -- it is a full-page advert, a picture page, or a section divider.
    if words < 120:
        return verdict(
            "sparse", 0.85,
            f"only {words} words on the whole page -- a full-page advert, "
            f"picture page or divider",
        )

    if table_hits >= 2 and digit_ratio > 0.14:
        return verdict(
            "tabular", 0.85,
            f"{digit_ratio:.0%} digits and {table_hits} table headings -- "
            f"market data, scores or weather",
        )

    if digit_ratio > 0.24 and density < 2.0:
        return verdict(
            "tabular", 0.7,
            f"{digit_ratio:.0%} of characters are digits with little prose "
            f"({density:.1f} sentences per 100 words) -- probably a data table",
        )

    if junk_hits >= 2 and density < 2.0:
        return verdict(
            "notices", 0.8,
            f"{junk_hits} classified/notice headings and only {density:.1f} "
            f"sentences per 100 words -- classifieds or public notices",
        )

    if density < 1.2:
        return verdict(
            "listings", 0.6,
            f"only {density:.1f} sentences per 100 words -- fragments rather "
            f"than prose, so probably listings",
        )

    return verdict(
        "articles", 0.8,
        f"{words} words at {density:.1f} sentences per 100 words -- "
        f"reads like reporting",
    )


def classify_document(doc: PdfDocument) -> list[PageVerdict]:
    return [classify_page(page) for page in doc.pages]


def _cluster_columns(words: list[dict], page_width: float) -> list[list[dict]]:
    """Group words into columns by finding vertical whitespace gutters.

    Builds a histogram of horizontal ink coverage, finds runs of empty
    columns wide enough to be gutters, and splits on those.
    """
    if not words:
        return []

    bucket_width = 4.0
    n_buckets = max(1, int(page_width / bucket_width) + 1)
    occupied = [False] * n_buckets

    for w in words:
        start = max(0, int(w["x0"] / bucket_width))
        end = min(n_buckets - 1, int(w["x1"] / bucket_width))
        for i in range(start, end + 1):
            occupied[i] = True

    # A gutter is >= 12pt of horizontal whitespace between inked regions.
    min_gutter_buckets = max(2, int(12.0 / bucket_width))

    boundaries: list[float] = [0.0]
    run_start: int | None = None
    for i, ink in enumerate(occupied):
        if not ink:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                run_len = i - run_start
                # Ignore leading margin; only split on interior gutters.
                if run_len >= min_gutter_buckets and run_start > 0:
                    boundaries.append((run_start + run_len / 2) * bucket_width)
                run_start = None
    boundaries.append(page_width)

    if len(boundaries) <= 2:
        return [words]

    columns: list[list[dict]] = [[] for _ in range(len(boundaries) - 1)]
    for w in words:
        centre = (w["x0"] + w["x1"]) / 2
        for idx in range(len(boundaries) - 1):
            if boundaries[idx] <= centre < boundaries[idx + 1]:
                columns[idx].append(w)
                break
        else:
            columns[-1].append(w)

    return [c for c in columns if c]


def _words_to_lines(words: list[dict], y_tolerance: float = 3.0) -> str:
    """Reassemble words into lines, reading top-to-bottom, left-to-right."""
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: list[list[dict]] = []
    for w in ordered:
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= y_tolerance:
            lines[-1].append(w)
        else:
            lines.append([w])
    return "\n".join(
        " ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"]))
        for line in lines
    )


def _column_ordered_text(page: pdfplumber.page.Page) -> str:
    words = page.extract_words(
        keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"]
    )
    columns = _cluster_columns(words, page.width or 612.0)
    if not columns:
        return ""
    chunks = [_words_to_lines(col) for col in columns]
    return "\n\n[COLUMN BREAK]\n\n".join(c for c in chunks if c.strip())


_DATE_PATTERNS = [
    # 12 March 2026 / 12 Mar 2026
    (
        re.compile(
            r"\b(\d{1,2})\s+"
            r"(january|february|march|april|may|june|july|august|september|october|"
            r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
            r"\.?,?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        ("d", "m", "y"),
    ),
    # March 12, 2026
    (
        re.compile(
            r"\b(january|february|march|april|may|june|july|august|september|october|"
            r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
            r"\.?\s+(\d{1,2}),?\s+(\d{4})\b",
            re.IGNORECASE,
        ),
        ("m", "d", "y"),
    ),
]

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def guess_edition_date(first_page_text: str) -> date | None:
    """Pull the edition date off the masthead, if it is there."""
    head = first_page_text[:3000]
    for pattern, order in _DATE_PATTERNS:
        match = pattern.search(head)
        if not match:
            continue
        parts = dict(zip(order, match.groups()))
        try:
            month = _MONTHS[parts["m"].lower()]
            return date(int(parts["y"]), month, int(parts["d"]))
        except (KeyError, ValueError):
            continue
    return None


def guess_source_name(first_page_text: str) -> str | None:
    """Guess the paper's name from the first non-trivial masthead line."""
    for raw in first_page_text.splitlines():
        line = raw.strip()
        if len(line) < 4 or len(line) > 60:
            continue
        if sum(c.isdigit() for c in line) > len(line) / 3:
            continue
        if "[COLUMN BREAK]" in line or line.startswith("==="):
            continue
        return line
    return None


def extract_pdf(
    path: Path,
    max_pages: int = 0,
    pages_wanted: set[int] | None = None,
    include_layout: bool = True,
) -> PdfDocument:
    """Extract page text.

    ``pages_wanted`` selects specific 1-based page numbers; everything else is
    left unread, which is the cheap way to skip advertising and listings
    sections. ``max_pages`` caps how many pages are read and applies after the
    selection.
    """
    pages: list[PageText] = []
    skipped_by_selection: list[int] = []
    with pdfplumber.open(str(path)) as pdf:
        total = len(pdf.pages)
        wanted = (
            sorted(n for n in pages_wanted if 1 <= n <= total)
            if pages_wanted
            else list(range(1, total + 1))
        )
        if pages_wanted:
            skipped_by_selection = [n for n in range(1, total + 1) if n not in wanted]
        if max_pages > 0:
            wanted = wanted[:max_pages]

        for page_number in wanted:
            index = page_number - 1
            page = pdf.pages[index]
            layout_text = (page.extract_text(layout=True) or "") if include_layout else ""
            column_text = _column_ordered_text(page)
            best = max(len(layout_text.strip()), len(column_text.strip()))
            # Whether the page has a real text layer at all must not depend
            # on include_layout, and must not be fooled by layout_text's
            # whitespace padding (extract_text(layout=True) preserves visual
            # position, so even a tiny ad can pad out to thousands of
            # characters). page.chars is the raw, already-parsed character
            # list pdfplumber built while opening the page -- reading its
            # length is free and reflects genuine extractable content
            # regardless of which rendering(s) were requested.
            has_text_layer = len(page.chars) > 0
            pages.append(
                PageText(
                    page_number=index + 1,
                    layout_text=layout_text,
                    column_text=column_text,
                    char_count=best,
                    has_text_layer=has_text_layer,
                )
            )

    first_text = pages[0].column_text if pages else ""
    return PdfDocument(
        path=path,
        page_count=total,
        pages=pages,
        source_name=guess_source_name(first_text),
        edition_date=guess_edition_date(first_text),
        selected=set(pages_wanted) if pages_wanted else None,
        skipped_by_selection=skipped_by_selection,
    )


def pdf_page_count(path: Path) -> int:
    """How many pages the PDF has, without extracting any text."""
    with pdfplumber.open(str(path)) as pdf:
        return len(pdf.pages)


def resolve_selection(
    path: Path,
    *,
    pages: str | None = None,
    skip_pages: str | None = None,
) -> set[int] | None:
    """Turn --pages / --skip-pages into the set of pages to read.

    Returns None when every page is wanted, so the caller can skip the
    filtering entirely. Raises PageSpecError on a bad selector.
    """
    if not pages and not skip_pages:
        return None

    total = pdf_page_count(path)
    wanted = parse_page_spec(pages, total) if pages else set(range(1, total + 1))
    if skip_pages:
        wanted -= parse_page_spec(skip_pages, total)
    if not wanted:
        raise PageSpecError(
            "that selection leaves no pages to process"
            + (f" (--pages {pages}" if pages else "")
            + (f" --skip-pages {skip_pages}" if skip_pages else "")
            + (")" if pages or skip_pages else "")
        )
    return wanted
