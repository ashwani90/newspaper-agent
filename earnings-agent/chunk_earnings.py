"""Build LLM chat prompts from a financial PDF (earnings release, investor
deck, 10-Q/10-K, results presentation) -- a standalone sibling of the
newspaper-agent project's manual chunking workflow, but tuned for financial
disclosures instead of news articles.

This file is fully self-contained: it does not import anything from
newspaper-agent, and nothing in newspaper-agent imports from it. The two
projects just happen to share the same shape of workflow (PDF -> page text ->
character-budget-sized chunks -> paste into an LLM chat -> save the reply).

Usage
-----
    python chunk_earnings.py <pdf> [--pages SPEC] [--skip-pages SPEC]
                              [--max-chars N] [--pages-per-chunk N]
                              [--out DIR]

    python chunk_earnings.py pages <pdf>          # per-page stats, to help pick pages

Page selectors accept "1-4", "7", "10-12", "-6", "8-", "odd", "even", and
comma- or space-separated combinations of those, e.g. "1-3,5,9-".

What it does
------------
1. Extracts text per page with pdfplumber (layout-preserving mode, since
   financial statements are table-heavy and column alignment carries meaning).
2. Packs the selected pages into numbered prompt files under a character
   budget (a smaller prompt means a reply that is less likely to be cut off).
3. Each prompt file contains instructions for extracting financial metrics,
   guidance, management commentary and risk factors as verbatim-anchored
   blocks, plus the page text itself.
4. Writes chunk-01.txt, chunk-02.txt, ... and a README.txt into an output
   folder, ready to paste into a chat window one at a time.

There is no "load" step (yet) -- this script only builds the prompts. What
you do with the replies is up to you; the block format below is designed so
a future loader could parse it the same way newspaper-agent parses article
replies, anchoring each item's full text back into the page it came from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

# PDFs routinely carry Unicode punctuation (curly quotes, en/em dashes, the
# non-breaking hyphen U+2011) that isn't representable in the Windows
# terminal's default cp1252 encoding -- printing a page preview or a chunk
# summary would otherwise crash with UnicodeEncodeError. utf-8 with
# replacement covers every platform's default without changing behaviour
# where stdout is already utf-8 (Linux/macOS).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

try:
    import pdfplumber
except ImportError:
    print(
        "error: pdfplumber is not installed for this Python interpreter.\n"
        "Either install it here (pip install pdfplumber) or run this script\n"
        "with an interpreter that already has it -- e.g. the newspaper-agent\n"
        "venv works fine since this is just a dependency, not shared code:\n"
        "  E:\\newspaper-agent\\.venv\\Scripts\\python.exe chunk_earnings.py ...",
        file=sys.stderr,
    )
    raise SystemExit(1)


# --------------------------------------------------------------------------- #
# Page selection (1-4, 7, 10-12, -6, 8-, odd, even)
# --------------------------------------------------------------------------- #


class PageSpecError(ValueError):
    pass


def parse_page_spec(spec: str, page_count: int | None = None) -> set[int]:
    if not spec or not spec.strip():
        return set()

    pages: set[int] = set()
    for raw in re.split(r"[,\s]+", spec.strip()):
        token = raw.strip().lower()
        if not token:
            continue

        if token in {"odd", "even"}:
            if not page_count:
                raise PageSpecError(f"'{token}' needs to know how many pages the PDF has")
            want_odd = token == "odd"
            pages.update(n for n in range(1, page_count + 1) if (n % 2 == 1) is want_odd)
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
                raise PageSpecError(f"open-ended range '{raw}' needs to know the page count")
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
            f"could not understand '{raw}' -- use forms like 1-4, 7, 10-12, 8-, odd, even"
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


def _compact_pages(numbers: list[int]) -> str:
    if not numbers:
        return "none"
    ordered = sorted(set(numbers))
    runs: list[tuple[int, int]] = [(ordered[0], ordered[0])]
    for value in ordered[1:]:
        start, end = runs[-1]
        if value == end + 1:
            runs[-1] = (start, value)
        else:
            runs.append((value, value))
    return ", ".join(str(s) if s == e else f"{s}-{e}" for s, e in runs)


# --------------------------------------------------------------------------- #
# PDF extraction
# --------------------------------------------------------------------------- #


@dataclass
class PageText:
    page_number: int
    text: str
    char_count: int
    has_text_layer: bool


def extract_pages(path: Path, pages_wanted: set[int] | None = None) -> list[PageText]:
    """Extract per-page text. Financial PDFs are typically single-column, so
    unlike the newspaper-agent extractor there is no column-splitting pass --
    layout=True alone keeps table rows and figures aligned, which matters more
    here than column order does.
    """
    pages: list[PageText] = []
    with pdfplumber.open(str(path)) as pdf:
        total = len(pdf.pages)
        wanted = (
            sorted(n for n in pages_wanted if 1 <= n <= total)
            if pages_wanted
            else list(range(1, total + 1))
        )
        for page_number in wanted:
            page = pdf.pages[page_number - 1]
            text = page.extract_text(layout=True) or ""
            has_text_layer = len(page.chars) > 0
            pages.append(
                PageText(
                    page_number=page_number,
                    text=text,
                    char_count=len(text.strip()),
                    has_text_layer=has_text_layer,
                )
            )
    return pages


def pdf_page_count(path: Path) -> int:
    with pdfplumber.open(str(path)) as pdf:
        return len(pdf.pages)


def resolve_selection(path: Path, *, pages: str | None, skip_pages: str | None) -> set[int] | None:
    if not pages and not skip_pages:
        return None
    total = pdf_page_count(path)
    wanted = parse_page_spec(pages, total) if pages else set(range(1, total + 1))
    if skip_pages:
        wanted -= parse_page_spec(skip_pages, total)
    if not wanted:
        raise PageSpecError("that selection leaves no pages to process")
    return wanted


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Prompt building
# --------------------------------------------------------------------------- #

DEFAULT_CHUNK_CHARS = 50_000

INSTRUCTIONS = """\
You are reading text extracted from a financial document -- an earnings
release, investor presentation, or a quarterly/annual filing. Your job is to
pull out every reported financial metric, piece of guidance, management
commentary and risk disclosure on the pages below, as individual items.

The text may include tables; numbers may be spaced out to preserve column
alignment from the original layout, so a single reported figure can have
extra whitespace around it.

WHAT COUNTS AS AN ITEM
- A financial metric with a reported value: revenue, net income, EPS (basic
  or diluted), operating/gross/net margin, EBITDA, free cash flow, segment or
  geography revenue, same-store sales, backlog, ARR, user/subscriber counts,
  debt, cash balance, capex, dividends, buybacks -- anything with a number
  attached to a named line item and a period.
- Forward guidance or outlook: a stated expectation, target, or range for a
  future period.
- Management commentary: a quote or paraphrased statement from an executive
  explaining a result, a strategic decision, or an outlook.
- A risk factor or a flagged uncertainty: anything the document itself calls
  out as a risk, contingency, or material change since the last filing.
- A one-time or non-recurring item: an impairment, a restructuring charge, a
  legal settlement, a divestiture gain/loss.

WHAT TO LEAVE OUT ENTIRELY
- Legal boilerplate: forward-looking-statement safe-harbor language, standard
  disclaimers, "this presentation does not constitute an offer" notices.
- Page furniture: headers, footers, page numbers, table of contents, investor
  contact information, logos, slide titles with no content beneath them.
- Auditor boilerplate UNLESS it states a qualified, adverse, or disclaimer
  opinion, or identifies a material weakness -- that IS a risk item, include it.
- Repeated definitions of non-GAAP terms (e.g. a standing "what we mean by
  adjusted EBITDA" box), unless this is the only place the definition appears
  and the number cannot be understood without it.

If a whole page turns out to be nothing but the above, reply with the single
line NO ITEMS ON THIS PAGE and nothing else. That is a correct and useful
answer -- never invent an item to fill the space.

HOW TO REPORT NUMBERS
- Use ONLY the figures and words on the page. Never add outside knowledge,
  never compute a number the document does not state, and never state as
  fact something the document hedges or attributes to a forecast.
- Copy the unit and currency exactly as printed ($, Rs, crore, lakh, %, bps,
  million, billion, thousands -- whatever the document uses).
- When the document gives a prior-period comparison or a percentage change,
  report it; when it does not, omit that field rather than computing one.

THE ANCHOR LINE
For each item, ANCHOR must be 8-15 words copied EXACTLY as they appear in the
page text above (not paraphrased, not reformatted) -- ideally the label and
value together, e.g. "Total revenue increased 18% year-over-year to $412".
This is how the text is later re-located in the source page, so it must be
verbatim, whitespace and all quirks included.

OUTPUT FORMAT
Reply with ONLY item blocks in exactly this shape. No preamble, no closing
remarks, no markdown headings other than the ### markers.

### ITEM
PAGE: 1
SECTION: Income Statement | Balance Sheet | Cash Flow | Segment Results | Guidance | Management Commentary | Risk Factor | KPI
LABEL: the metric or topic name, e.g. "Total Revenue", "FY2027 Revenue Guidance"
PERIOD: the period it covers, e.g. "Q2 FY2026" or "twelve months ended March 31, 2026"
VALUE: the reported figure, verbatim including unit/currency, or omit for a purely qualitative item
PRIOR_VALUE: the prior-period figure for the same metric, or omit if not stated
CHANGE: the stated or shown change, e.g. "+18% YoY", or omit
ANCHOR: 8-15 words copied verbatim from the page text
SUMMARY: one sentence, plain, using only what the page states
WHY: one sentence on why it matters, or omit if nothing beyond the number itself
### END

Repeat that block for every item on the page(s) below.
"""

# --------------------------------------------------------------------------- #
# IPO / public-issue announcement pages
# --------------------------------------------------------------------------- #

# Newspapers regularly carry a full-page statutory notice when a company
# launches an IPO -- a "public announcement", an abridged prospectus summary,
# or a price-band/risk-factor notice. These are financial disclosures too,
# but their content (issue dates, price band, lead managers, numbered risk
# factors) does not fit the earnings-report template above, so a page that
# looks like one of these gets routed to its own ipo-chunk-*.txt files with
# a template built for it, instead of chunk-*.txt.
_IPO_MARKERS = (
    "red herring prospectus", "price band", "bid/issue", "book running lead manager",
    "asba", "anchor investor", "initial public offer", "qib portion",
    "issue opens", "issue closes", "registrar to the issue",
    "draft red herring prospectus", "not for release, publication or distribution",
    "equity shares of face value", "basis of allotment", "abridged prospectus",
)


def looks_like_ipo_page(text: str) -> bool:
    """Heuristic: does this page read like an IPO/public-issue announcement?

    Deliberately requires several distinct markers, not just one -- a single
    passing mention (e.g. a news article that quotes a price band) should not
    get pulled out of the regular chunk stream.
    """
    lowered = (text or "").lower()
    hits = sum(1 for marker in _IPO_MARKERS if marker in lowered)
    return hits >= 3


IPO_INSTRUCTIONS = """\
You are reading text extracted from a newspaper page carrying an IPO / public
issue announcement -- the kind of full-page statutory notice a company
publishes when launching an Initial Public Offering (a "public announcement",
an abridged prospectus summary, or a red-herring-prospectus notice). Your job
is to pull out the specific facts an investor would look for, as individual
items.

WHAT COUNTS AS AN ITEM
- Issue identity: company name, promoter(s), which stock exchanges it lists
  on (BSE/NSE), and the issue type (fresh issue / offer for sale / both).
- Pricing: price band (floor/cap), face value, lot size, minimum bid amount.
- Size: fresh issue amount, OFS amount, total issue size, post-issue market
  capitalisation at floor and cap price.
- Dates: anchor investor bidding date, bid/issue opening and closing dates,
  basis-of-allotment date, refund/unblocking date, credit-of-shares date,
  listing date.
- Participants: book running lead manager(s), registrar to the issue,
  statutory auditor (if named).
- Objects of the issue: what the fresh-issue proceeds will be used for.
- Financial highlights: revenue, profit/loss, and any ratio given for recent
  fiscal years (P/E, return on net worth, weighted average cost of
  acquisition, debt service coverage ratio, etc.).
- Risk factors: each one reported separately, using its own numbered heading
  exactly as printed, with a one-sentence summary of what it says.
- Category-wise reservation: QIB / Non-Institutional / Retail portion shares.

WHAT TO LEAVE OUT
- Boilerplate compliance language ("this is a public announcement and not a
  prospectus", "not for release outside India", jurisdiction notices) and
  repeated masthead/footer text.
- Anything on the same page that is clearly unrelated to this specific issue
  (another article, an unrelated ad) -- report only items about this IPO.

HOW TO REPORT
- Use ONLY the figures and words on the page; never add outside knowledge.
- Copy currency/unit exactly as printed (Rs, INR, ₹, crore, lakh, %, x times).
- Use the exact date format and wording printed.

THE ANCHOR LINE
For each item, ANCHOR must be 8-15 words copied EXACTLY as they appear in the
page text above -- for a risk factor, its numbered heading plus the first
few words of its body. This is how the text is later re-located on the page,
so it must be verbatim.

OUTPUT FORMAT
Reply with ONLY item blocks in exactly this shape. No preamble, no closing
remarks, no markdown headings other than the ### markers.

### ITEM
PAGE: 1
SECTION: Issue Identity | Pricing | Size | Dates | Participants | Objects of Issue | Financials | Risk Factor | Reservation
LABEL: short label, e.g. "Price Band", "Bid/Issue Closing Date", "Risk Factor 3: Debt Servicing and Financing Risk"
VALUE: the reported figure or fact, verbatim, or omit for a purely qualitative item
ANCHOR: 8-15 words copied verbatim from the page text
SUMMARY: one sentence, plain, using only what the page states
WHY: one sentence on why it matters to an investor, or omit
### END

If the page turns out to have no IPO/public-issue content at all, reply with
the single line NO IPO CONTENT ON THIS PAGE and nothing else.
"""

# --------------------------------------------------------------------------- #
# Published financial-results table pages
# --------------------------------------------------------------------------- #

# Listed companies routinely publish a statutory extract of their quarterly/
# annual results in newspapers (SEBI LODR Regulation 33/52) -- a compact
# table of income-statement line items across a few periods, standalone
# and/or consolidated. Different shape again from both the earnings-report
# template and the IPO template, so it gets its own results-chunk-*.txt.
_RESULTS_MARKERS = (
    "financial results for the quarter", "unaudited financial results",
    "audited financial results", "statement of standalone", "statement of consolidated",
    "extract of standalone", "extract of consolidated", "total income from operations",
    "net profit/(loss)", "net profit / (loss)", "earnings per share",
    "paid up equity share capital", "paid-up equity share capital",
    "regulation 33", "regulation 52", "limited review",
    "board of directors at its meeting", "corresponding quarter", "quarter ended",
    "year ended",
)


def looks_like_results_page(text: str) -> bool:
    """Heuristic: does this page carry a published financial-results table?

    Requires more hits than the IPO check (several of these markers, e.g.
    "quarter ended", are common enough on their own to show up in an
    ordinary business article) so an isolated mention does not misfire.
    """
    lowered = (text or "").lower()
    hits = sum(1 for marker in _RESULTS_MARKERS if marker in lowered)
    return hits >= 4


RESULTS_INSTRUCTIONS = """\
You are reading text extracted from a newspaper page carrying a company's
published financial-results table -- the statutory extract listed companies
publish under stock-exchange disclosure rules (SEBI LODR Regulation 33/52),
showing standalone or consolidated results for a quarter and/or year, with
one or more comparative periods.

WHAT COUNTS AS AN ITEM
- Header facts: company name, whether the results are standalone or
  consolidated, audited/unaudited (or subject to limited review), the
  period(s) covered (e.g. "quarter ended June 30, 2026", "year ended March
  31, 2026"), and the unit the table is stated in (Rs lakh, Rs crore, etc.).
- Each reported line item with its value(s) across the periods shown: total
  income / revenue from operations, other income, total expenses, profit
  before tax, tax expense, net profit/(loss) for the period, other
  comprehensive income, total comprehensive income, paid-up equity share
  capital, reserves (excluding revaluation reserve), earnings per share
  (basic and diluted).
- Segment-wise revenue/results/assets/liabilities, if a segment table is
  present.
- Notes: any exceptional/extraordinary item, a qualified or adverse auditor
  remark, a restatement, or a note explaining a one-time gain/loss.
- Corporate actions mentioned alongside the results: dividend declared or
  recommended, the board meeting date, any other resolution passed at the
  same meeting.

WHAT TO LEAVE OUT
- Boilerplate: "the above is an extract of the detailed format... filed with
  the stock exchange(s)... available on the website", bare regulation
  citations with no figures attached, registered-office/contact details, an
  auditor's name with no finding stated.
- Any other article or notice on the same page unrelated to this table.

HOW TO REPORT
- Copy every number and label exactly as printed, including sign and
  parentheses for a loss, e.g. "(1,245.30)".
- When both standalone and consolidated columns appear, report each line
  item once per basis -- use BASIS to say which.
- When both a quarter column and a year-to-date/annual column appear, report
  each period separately; never average or combine them.
- Never compute a percentage change yourself; report one only if the table
  itself states it.

THE ANCHOR LINE
For each item, ANCHOR must be 8-15 words copied EXACTLY as they appear in the
page text above -- ideally the row label and its first value together, e.g.
"Total income from operations 1,245.30 1,102.60 987.40". This is how the
text is later re-located on the page, so it must be verbatim, spacing
included.

OUTPUT FORMAT
Reply with ONLY item blocks in exactly this shape. No preamble, no closing
remarks, no markdown headings other than the ### markers.

### ITEM
PAGE: 1
SECTION: Header | Income Statement | Per Share Data | Segment | Note | Corporate Action
BASIS: Standalone | Consolidated | (omit if the table does not distinguish)
LABEL: the row/line-item name exactly as printed, e.g. "Net Profit/(Loss) for the period"
PERIOD: the column heading it came from, e.g. "Quarter ended June 30, 2026" or "Year ended March 31, 2026"
VALUE: the reported figure, verbatim including sign/parentheses and unit
PRIOR_VALUE: the comparative figure for the same row from another column on the same table, or omit
ANCHOR: 8-15 words copied verbatim from the page text
SUMMARY: one sentence, plain, using only what the table/page states
WHY: one sentence on why it matters (e.g. swung to a loss, margin moved), or omit
### END

If the page has no financial-results table on it at all, reply with the
single line NO FINANCIAL RESULTS ON THIS PAGE and nothing else.
"""


@dataclass
class PromptChunk:
    index: int
    total: int
    page_numbers: list[int]
    text: str
    kind: str = "chunk"

    @property
    def filename(self) -> str:
        return f"{self.kind}-{self.index:02d}.txt"


def build_prompt_chunks(
    pages: list[tuple[int, str]],
    *,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    pages_per_chunk: int | None = None,
    instructions: str = INSTRUCTIONS,
    kind: str = "chunk",
    section_label: str = "DOCUMENT TEXT",
) -> list[PromptChunk]:
    """Pack page text into paste-sized prompt files, same packing rule as
    newspaper-agent's build_prompts: by character budget by default, or a
    fixed page count per chunk when requested. A single page over budget
    still gets its own chunk rather than being split mid-table.

    ``instructions`` / ``kind`` / ``section_label`` let a different page
    category (e.g. IPO announcement pages) reuse this same packer with its
    own prompt template and its own chunk-file naming.
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
        for page_no, text in pages:
            if current and size + len(text) > max_chars:
                groups.append(current)
                current, size = [], 0
            current.append((page_no, text))
            size += len(text)
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
        body = "\n\n".join(text for _, text in group)
        chunks.append(
            PromptChunk(
                index=index,
                total=total,
                page_numbers=page_numbers,
                kind=kind,
                text=(
                    f"{header}\n\n{instructions}\n\n"
                    f"{'=' * 70}\n{section_label} ({span})\n{'=' * 70}\n\n{body}\n"
                ),
            )
        )
    return chunks


def _readme_text(pdf: Path, out_dir: Path, chunks: list[PromptChunk]) -> str:
    regular = [c for c in chunks if c.kind == "chunk"]
    ipo = [c for c in chunks if c.kind == "ipo-chunk"]
    results = [c for c in chunks if c.kind == "results-chunk"]
    lines: list[str] = [f"Prompts for {pdf.name}"]

    if regular:
        lines.append("")
        lines.append(f"{len(regular)} financial-metric prompt file(s):")
        lines.extend(
            f"  {c.filename}   pages {c.page_numbers[0]}-{c.page_numbers[-1]}"
            for c in regular
        )
    if ipo:
        lines.append("")
        lines.append(
            f"{len(ipo)} IPO/public-issue-announcement prompt file(s) "
            f"(detected automatically):"
        )
        lines.extend(
            f"  {c.filename}   pages {c.page_numbers[0]}-{c.page_numbers[-1]}"
            for c in ipo
        )
    if results:
        lines.append("")
        lines.append(
            f"{len(results)} published-financial-results prompt file(s) "
            f"(detected automatically):"
        )
        lines.extend(
            f"  {c.filename}   pages {c.page_numbers[0]}-{c.page_numbers[-1]}"
            for c in results
        )

    lines.append("")
    lines.append("WHAT TO DO")
    lines.append(
        "1. Open a chunk file, copy all of it, paste it into your LLM chat window."
    )
    lines.append(
        "2. Copy the whole reply and save it next to it, e.g. chunk-01.txt -> "
        "reply-01.txt, ipo-chunk-01.txt -> ipo-reply-01.txt, "
        "results-chunk-01.txt -> results-reply-01.txt."
    )
    lines.append("3. Repeat for each chunk.")
    lines.append("")
    lines.append(
        f"There is still no database -- once you've saved your replies here, run:\n"
        f"  chunk_earnings.py report \"{out_dir}\"\n"
        f"to build a browsable HTML page from them (see --help for details)."
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Reading replies back: a browsable report, not a database
# --------------------------------------------------------------------------- #
#
# There's still no loader into a database (see the module docstring) -- but a
# pile of reply-*.txt files isn't something anyone can "effectively see and
# understand" as plain text either, especially once there are dozens of them.
# This parses every reply's ### ITEM blocks (tolerating the same markdown
# mangling newspaper-agent's parser tolerates) and renders them into one
# self-contained HTML file with no server and no external resources, so it
# opens straight from disk (file://) via a plain double-click.

_REPLY_KIND_GLOBS = (
    ("chunk", "reply-*.txt"),
    ("ipo", "ipo-reply-*.txt"),
    ("results", "results-reply-*.txt"),
)

_ITEM_FIELD_KEYS = (
    "PAGE", "SECTION", "BASIS", "LABEL", "PERIOD", "VALUE", "PRIOR_VALUE",
    "PRIOR VALUE", "CHANGE", "ANCHOR", "SUMMARY", "WHY",
)
_ITEM_START = re.compile(r"^\s*#{2,4}\s*ITEM\b", re.IGNORECASE)
_ITEM_END = re.compile(r"^\s*#{2,4}\s*END\b", re.IGNORECASE)
_ITEM_LEADING_JUNK = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?[*_`]*\s*")
_ITEM_KEY_LINE = re.compile(
    r"^(" + "|".join(_ITEM_FIELD_KEYS) + r")\s*[:\-]\s*(.*)$", re.IGNORECASE
)


def _clean_item_value(value: str) -> str:
    text = value.strip()
    text = re.sub(r"[*_`]+$", "", text).strip()
    text = re.sub(r"^[*_`]+", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def parse_reply(text: str, *, kind: str, source: str) -> list[dict]:
    """Parse one reply file's ### ITEM blocks into plain dicts.

    Deliberately loose, like newspaper-agent's reply parser: unknown lines
    are ignored rather than fatal, a reply truncated mid-item still yields
    every complete item before the cut, and a wrapped SUMMARY/WHY line that
    lost its line break in a chat window is folded back onto the field it
    continues rather than dropped.
    """
    items: list[dict] = []
    current: dict | None = None
    normalised = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n")

    for line in normalised.split("\n"):
        if _ITEM_START.match(line):
            if current:
                items.append(current)
            current = {"kind": kind, "source": source}
            continue
        if _ITEM_END.match(line):
            if current:
                items.append(current)
            current = None
            continue
        if current is None:
            continue

        candidate = _ITEM_LEADING_JUNK.sub("", line)
        match = _ITEM_KEY_LINE.match(candidate)
        if match:
            key = match.group(1).upper().replace(" ", "_")
            value = _clean_item_value(match.group(2))
            if value:
                current[key] = value
            continue

        # A continuation line of a multi-line SUMMARY (or WHY, whichever was
        # populated most recently) -- chat windows sometimes wrap these.
        if line.strip():
            for field_name in ("SUMMARY", "WHY"):
                if field_name in current:
                    current[field_name] = f"{current[field_name]} {line.strip()}"
                    break

    if current:
        items.append(current)
    return items


def load_replies(folder: Path) -> tuple[list[dict], dict[str, int]]:
    """Parse every reply file in a prompts folder. Returns (items, counts-by-kind)."""
    items: list[dict] = []
    counts = {kind: 0 for kind, _ in _REPLY_KIND_GLOBS}
    seen: set[str] = set()
    for kind, pattern in _REPLY_KIND_GLOBS:
        for path in sorted(folder.glob(pattern)):
            if path.name in seen:
                continue
            seen.add(path.name)
            text = path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_reply(text, kind=kind, source=path.name)
            items.extend(parsed)
            counts[kind] += len(parsed)
    return items, counts


_KIND_LABELS = {"chunk": "Financial Metric", "ipo": "IPO Announcement", "results": "Published Results"}

_REPORT_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__ -- extracted items</title>
<style>
  :root {
    --bg: #f4f5f7; --panel: #ffffff; --ink: #1c2430; --ink-soft: #5b6675;
    --ink-faint: #8b95a3; --line: #e3e6ea; --accent: #1a5fb4; --accent-bg: #e8f0fc;
    --warn-bg: #fff4e5; --warn-ink: #8a5300;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font-size: 14px; line-height: 1.5; }
  header {
    position: sticky; top: 0; z-index: 5; background: var(--panel);
    border-bottom: 1px solid var(--line); padding: 14px 24px;
    display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  }
  header h1 { font-size: 17px; margin: 0; font-weight: 700; }
  header .stats { font-size: 12.5px; color: var(--ink-soft); }
  .toolbar {
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
    margin-left: auto;
  }
  .toolbar input[type="search"], .toolbar select {
    border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px;
    font-size: 13px; background: var(--panel); color: var(--ink);
  }
  .toolbar input[type="search"] { width: 220px; }
  main { max-width: 980px; margin: 0 auto; padding: 20px 24px 80px; }
  .section-group { margin-bottom: 22px; }
  .section-title {
    display: flex; align-items: center; gap: 10px; margin: 0 0 10px;
    font-size: 12.5px; font-weight: 700; letter-spacing: 0.04em;
    text-transform: uppercase; color: var(--ink-soft);
  }
  .section-title .count {
    background: var(--line); color: var(--ink-soft); border-radius: 999px;
    padding: 1px 9px; font-size: 11px; font-weight: 700;
  }
  .item-card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
    padding: 14px 16px; margin-bottom: 8px;
  }
  .item-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .item-label { font-weight: 700; font-size: 14.5px; }
  .item-period { color: var(--ink-soft); font-size: 12.5px; }
  .item-basis {
    font-size: 10.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.03em; color: var(--accent); background: var(--accent-bg);
    border-radius: 999px; padding: 1px 8px;
  }
  .item-meta { margin-left: auto; font-size: 11.5px; color: var(--ink-faint); white-space: nowrap; }
  .item-values { margin-top: 6px; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .item-value { font-size: 19px; font-weight: 700; }
  .item-prior { font-size: 12.5px; color: var(--ink-faint); }
  .item-change { font-size: 12.5px; font-weight: 700; color: var(--accent); }
  .item-summary { margin: 8px 0 0; color: var(--ink); }
  .item-why {
    margin-top: 8px; background: var(--warn-bg); color: var(--warn-ink);
    border-radius: 8px; padding: 8px 11px; font-size: 12.5px;
  }
  .item-anchor-toggle {
    margin-top: 8px; font-size: 11.5px; color: var(--accent); cursor: pointer;
    background: none; border: none; padding: 0; font-family: inherit;
  }
  .item-anchor {
    display: none; margin-top: 6px; font-size: 12px; color: var(--ink-soft);
    background: var(--bg); border-radius: 8px; padding: 8px 11px; font-style: italic;
  }
  .item-anchor.is-open { display: block; }
  .item-card[hidden] { display: none; }
  .section-group[hidden] { display: none; }
  .empty { text-align: center; color: var(--ink-faint); padding: 60px 20px; }
  .kind-tag {
    font-size: 10.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.03em; color: var(--ink-soft);
  }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="stats">__STATS__</div>
  <div class="toolbar">
    <input type="search" id="q" placeholder="Search label, value, summary..." />
    <select id="sectionFilter"><option value="">All sections</option></select>
    __KIND_FILTER__
  </div>
</header>
<main id="main"></main>
<script id="report-data" type="application/json">__DATA_JSON__</script>
<script>
const items = JSON.parse(document.getElementById('report-data').textContent);
const main = document.getElementById('main');
const q = document.getElementById('q');
const sectionFilter = document.getElementById('sectionFilter');
const kindFilter = document.getElementById('kindFilter');

const sections = [...new Set(items.map(i => i.SECTION || 'Uncategorised'))].sort();
for (const s of sections) {
  const opt = document.createElement('option');
  opt.value = s; opt.textContent = s;
  sectionFilter.appendChild(opt);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

function matches(item, query) {
  if (!query) return true;
  const hay = [item.LABEL, item.VALUE, item.SUMMARY, item.WHY, item.PERIOD]
    .filter(Boolean).join(' ').toLowerCase();
  return hay.includes(query.toLowerCase());
}

function render() {
  const query = q.value.trim();
  const wantSection = sectionFilter.value;
  const wantKind = kindFilter ? kindFilter.value : '';

  const bySection = new Map();
  for (const item of items) {
    const section = item.SECTION || 'Uncategorised';
    if (wantSection && section !== wantSection) continue;
    if (wantKind && item.kind !== wantKind) continue;
    if (!matches(item, query)) continue;
    if (!bySection.has(section)) bySection.set(section, []);
    bySection.get(section).push(item);
  }

  main.innerHTML = '';
  if (!bySection.size) {
    main.innerHTML = '<div class="empty">No items match these filters.</div>';
    return;
  }

  for (const [section, group] of [...bySection.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const wrap = document.createElement('div');
    wrap.className = 'section-group';
    wrap.innerHTML = `<div class="section-title">${escapeHtml(section)} <span class="count">${group.length}</span></div>`;

    group.sort((a, b) => (parseInt(a.PAGE) || 0) - (parseInt(b.PAGE) || 0));

    for (const item of group) {
      const card = document.createElement('div');
      card.className = 'item-card';

      const valuesLine = (item.VALUE || item.PRIOR_VALUE || item.CHANGE) ? `
        <div class="item-values">
          ${item.VALUE ? `<span class="item-value">${escapeHtml(item.VALUE)}</span>` : ''}
          ${item.PRIOR_VALUE ? `<span class="item-prior">prior: ${escapeHtml(item.PRIOR_VALUE)}</span>` : ''}
          ${item.CHANGE ? `<span class="item-change">${escapeHtml(item.CHANGE)}</span>` : ''}
        </div>` : '';

      const anchorId = 'a' + Math.random().toString(36).slice(2);

      card.innerHTML = `
        <div class="item-head">
          <span class="item-label">${escapeHtml(item.LABEL || '(untitled)')}</span>
          ${item.PERIOD ? `<span class="item-period">${escapeHtml(item.PERIOD)}</span>` : ''}
          ${item.BASIS ? `<span class="item-basis">${escapeHtml(item.BASIS)}</span>` : ''}
          <span class="item-meta">${item.PAGE ? 'p' + escapeHtml(item.PAGE) : ''} &middot; <span class="kind-tag">${escapeHtml(item.kind || '')}</span></span>
        </div>
        ${valuesLine}
        ${item.SUMMARY ? `<p class="item-summary">${escapeHtml(item.SUMMARY)}</p>` : ''}
        ${item.WHY ? `<div class="item-why">${escapeHtml(item.WHY)}</div>` : ''}
        ${item.ANCHOR ? `
          <button class="item-anchor-toggle" data-target="${anchorId}">Show source text</button>
          <div class="item-anchor" id="${anchorId}">&ldquo;${escapeHtml(item.ANCHOR)}&hellip;&rdquo; -- ${escapeHtml(item.source || '')}</div>
        ` : ''}
      `;
      wrap.appendChild(card);
    }
    main.appendChild(wrap);
  }

  main.querySelectorAll('.item-anchor-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.getElementById(btn.dataset.target).classList.toggle('is-open');
    });
  });
}

q.addEventListener('input', render);
sectionFilter.addEventListener('change', render);
if (kindFilter) kindFilter.addEventListener('change', render);
render();
</script>
</body>
</html>
"""


def render_report_html(*, title: str, items: list[dict], counts: dict[str, int]) -> str:
    present_kinds = [kind for kind, count in counts.items() if count > 0]
    kind_filter_html = ""
    if len(present_kinds) > 1:
        options = "".join(
            f'<option value="{kind}">{_KIND_LABELS.get(kind, kind)}</option>'
            for kind in present_kinds
        )
        kind_filter_html = f'<select id="kindFilter"><option value="">All kinds</option>{options}</select>'

    stats_parts = [f"{len(items)} item(s)"]
    stats_parts.extend(
        f"{count} {_KIND_LABELS.get(kind, kind).lower()}"
        for kind, count in counts.items()
        if count > 0 and len(present_kinds) > 1
    )
    stats = " &middot; ".join(stats_parts)

    html = _REPORT_HTML_TEMPLATE
    html = html.replace("__TITLE__", title)
    html = html.replace("__STATS__", stats)
    html = html.replace("__KIND_FILTER__", kind_filter_html)
    html = html.replace("__DATA_JSON__", json.dumps(items))
    return html


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def cmd_pages(args: argparse.Namespace) -> int:
    """Per-page stats, to help pick which pages to chunk."""
    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"file not found: {pdf}", file=sys.stderr)
        return 1
    total = pdf_page_count(pdf)
    print(f"{pdf.name} -- {total} page(s)")
    for page in extract_pages(pdf):
        first_line = next((ln.strip() for ln in page.text.splitlines() if ln.strip()), "")
        flag = "" if page.has_text_layer else "  [NO TEXT LAYER -- needs OCR]"
        print(
            f"  p{page.page_number:>3}  {page.char_count:>6} chars  "
            f"{first_line[:70]!r}{flag}"
        )
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"file not found: {pdf}", file=sys.stderr)
        return 1

    try:
        wanted = resolve_selection(pdf, pages=args.pages, skip_pages=args.skip_pages)
    except PageSpecError as exc:
        print(f"bad page selection: {exc}", file=sys.stderr)
        return 2

    total = pdf_page_count(pdf)
    if wanted is not None:
        print(f"  page selection: {_compact_pages(sorted(wanted))} of {total}")

    print(f"  reading {pdf.name}")
    pages = extract_pages(pdf, pages_wanted=wanted)
    text_pages = [p for p in pages if p.has_text_layer]
    no_text = [p.page_number for p in pages if not p.has_text_layer]

    if not text_pages:
        print(
            "  no extractable text on any selected page -- this PDF is "
            "scanned images and needs OCR first",
            file=sys.stderr,
        )
        return 1

    if no_text:
        print(f"  NO TEXT LAYER  pages {_compact_pages(no_text)}  (needs OCR, skipped)")

    # Priority matters: a page is checked for the rarer, more specific
    # categories first so it is not double-counted if it happens to trip
    # both heuristics.
    ipo_pages = [p for p in text_pages if looks_like_ipo_page(p.text)]
    remaining = [p for p in text_pages if p not in ipo_pages]
    results_pages = [p for p in remaining if looks_like_results_page(p.text)]
    other_pages = [p for p in remaining if p not in results_pages]

    if ipo_pages:
        print(
            f"  IPO/public-issue announcement detected on page "
            f"{_compact_pages([p.page_number for p in ipo_pages])} -- "
            f"routed to ipo-chunk-*.txt"
        )
    if results_pages:
        print(
            f"  published financial results detected on page "
            f"{_compact_pages([p.page_number for p in results_pages])} -- "
            f"routed to results-chunk-*.txt"
        )

    chunks = build_prompt_chunks(
        [(p.page_number, p.text) for p in other_pages],
        max_chars=args.max_chars,
        pages_per_chunk=args.pages_per_chunk,
    )
    ipo_chunks = build_prompt_chunks(
        [(p.page_number, p.text) for p in ipo_pages],
        max_chars=args.max_chars,
        pages_per_chunk=args.pages_per_chunk,
        instructions=IPO_INSTRUCTIONS,
        kind="ipo-chunk",
        section_label="IPO / PUBLIC ISSUE ANNOUNCEMENT TEXT",
    )
    results_chunks = build_prompt_chunks(
        [(p.page_number, p.text) for p in results_pages],
        max_chars=args.max_chars,
        pages_per_chunk=args.pages_per_chunk,
        instructions=RESULTS_INSTRUCTIONS,
        kind="results-chunk",
        section_label="PUBLISHED FINANCIAL RESULTS TEXT",
    )
    all_chunks = chunks + ipo_chunks + results_chunks

    out_dir = Path(args.out) if args.out else Path(__file__).parent / "prompts" / pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("chunk-*.txt"):
        stale.unlink()
    for stale in out_dir.glob("ipo-chunk-*.txt"):
        stale.unlink()
    for stale in out_dir.glob("results-chunk-*.txt"):
        stale.unlink()

    for chunk in all_chunks:
        target = out_dir / chunk.filename
        target.write_text(chunk.text, encoding="utf-8")
        print(f"  wrote {chunk.filename} (pages {chunk.page_numbers[0]}-{chunk.page_numbers[-1]})")

    (out_dir / "README.txt").write_text(_readme_text(pdf, out_dir, all_chunks), encoding="utf-8")

    print()
    print(f"  pages           {len(text_pages)} with text / {len(pages)} read")
    if ipo_pages:
        print(f"  ipo pages       {len(ipo_pages)}")
    if results_pages:
        print(f"  results pages   {len(results_pages)}")
    print(f"  prompts written {len(all_chunks)}")
    print(f"  folder          {out_dir}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    target = Path(args.target)
    if target.is_file():
        folder = Path(__file__).parent / "prompts" / target.stem
    else:
        folder = target

    if not folder.exists():
        print(f"folder not found: {folder}", file=sys.stderr)
        return 1

    items, counts = load_replies(folder)
    if not items:
        print(
            f"no reply files found in {folder}\n"
            f"(looked for reply-*.txt, ipo-reply-*.txt, results-reply-*.txt)",
            file=sys.stderr,
        )
        return 1

    html = render_report_html(title=folder.name, items=items, counts=counts)
    out_path = folder / "report.html"
    out_path.write_text(html, encoding="utf-8")

    print(f"  items     {len(items)}")
    for kind, count in counts.items():
        if count:
            print(f"    {_KIND_LABELS.get(kind, kind):<18} {count}")
    print(f"  report    {out_path}")

    if not args.no_open:
        try:
            webbrowser.open(out_path.resolve().as_uri())
        except Exception:
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build LLM chat prompts from a financial PDF (earnings "
        "report, investor deck, filing) -- standalone, no API key needed."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pages", help="show per-page stats, to help pick pages")
    p.add_argument("pdf")
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser("prompt", help="build chunked prompts from the PDF")
    p.add_argument("pdf")
    p.add_argument("--pages", default=None, help="e.g. '1-4,7,10-12', 'odd', '8-'")
    p.add_argument("--skip-pages", default=None, help="same syntax, pages to leave out")
    p.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_CHUNK_CHARS,
        help=f"character budget per chunk (default {DEFAULT_CHUNK_CHARS})",
    )
    p.add_argument(
        "--pages-per-chunk",
        type=int,
        default=None,
        help="fixed pages per chunk instead of the character budget",
    )
    p.add_argument("--out", default=None, help="output folder (default: ./prompts/<pdf-stem>)")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser(
        "report",
        help="parse saved reply-*.txt files into a browsable HTML page",
    )
    p.add_argument(
        "target",
        help="a prompts folder (e.g. prompts/tata-motor), or the original PDF path",
    )
    p.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    p.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
