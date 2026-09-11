"""The ingest pipeline: PDF in, summarised and topic-tagged articles out.

    extract  ->  segment (LLM)  ->  stitch continuations  ->  store articles
             ->  summarise + tag (LLM)  ->  store summaries  ->  FTS reindex

Design notes:

* Ingest is idempotent by file hash. Re-running on the same PDF is a no-op
  unless you pass force=True.
* Ingest is resumable. Articles are committed page by page and summaries
  article by article, so a crash, a rate limit, or a Ctrl-C loses at most the
  item in flight. Re-run to pick up where it stopped.
* Topic tags from the model are validated against topics.txt; anything not on
  the list is discarded rather than trusted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import CONFIG
from .db import (
    Article,
    ArticleTopic,
    Edition,
    Page,
    Summary,
    Topic,
    find_edition_by_hash,
    reindex_article,
    session_scope,
    sha256_file,
)
from .extract import (
    PdfDocument,
    classify_document,
    extract_pdf,
    pdf_page_count,
    resolve_selection,
)
from .llm import segment_page, summarise_article
from .manual import (
    DEFAULT_CHUNK_CHARS,
    ParsedArticle,
    ParseResult,
    PromptChunk,
    build_prompts,
    parse_response,
    slice_bodies,
)
from .schemas import ExtractedArticle
from .topics import TopicSpec, keyword_matches, parse_topics_file, sync_topics

log = logging.getLogger(__name__)

# A "paragraph" shorter than this is treated as a stray fragment, not an
# article body worth summarising.
MIN_BODY_CHARS = 200

ProgressFn = Callable[[str], None]


@dataclass
class IngestReport:
    pdf: Path
    edition_id: int | None = None
    pages_seen: int = 0
    pages_with_text: int = 0
    image_only_pages: list[int] = field(default_factory=list)
    articles_found: int = 0
    articles_skipped_short: int = 0
    summaries_written: int = 0
    topic_tags: int = 0
    skipped_by_selection: list[int] = field(default_factory=list)
    skipped_as_junk: list[int] = field(default_factory=list)
    junk_reasons: dict[int, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_lines(self) -> list[str]:
        if self.skipped_reason:
            return [f"{self.pdf.name}: skipped ({self.skipped_reason})"]
        lines = [
            f"{self.pdf.name}",
            f"  pages           {self.pages_with_text} with text / {self.pages_seen} read",
            f"  articles        {self.articles_found}",
            f"  summaries       {self.summaries_written}",
            f"  topic tags      {self.topic_tags}",
        ]
        if self.skipped_by_selection:
            lines.append(
                f"  not selected    pages "
                f"{_compact_pages(self.skipped_by_selection)}"
            )
        if self.skipped_as_junk:
            lines.append(
                f"  skipped as junk pages {_compact_pages(self.skipped_as_junk)}"
            )
        if self.articles_skipped_short:
            lines.append(f"  fragments dropped {self.articles_skipped_short}")
        if self.image_only_pages:
            preview = ", ".join(str(p) for p in self.image_only_pages[:12])
            more = " ..." if len(self.image_only_pages) > 12 else ""
            lines.append(f"  NO TEXT LAYER   pages {preview}{more}  (needs OCR)")
        for err in self.errors:
            lines.append(f"  ERROR           {err}")
        return lines


def _noop(_msg: str) -> None:
    pass


def _compact_pages(numbers: list[int]) -> str:
    """Render [1,2,3,7,10,11,12] as "1-3, 7, 10-12" for readable reports."""
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
    return ", ".join(
        str(start) if start == end else f"{start}-{end}" for start, end in runs
    )


def _stitch_continuations(
    page_articles: list[tuple[int, ExtractedArticle]],
) -> list[tuple[int, ExtractedArticle]]:
    """Join articles that run across a page break.

    A newspaper story often starts on page 1 and finishes on page 4. The
    segmenter flags such fragments with is_continuation; here a flagged
    fragment is appended to the most recent article whose headline it
    plausibly continues, falling back to the immediately preceding article.
    """
    stitched: list[tuple[int, ExtractedArticle]] = []
    for page_no, art in page_articles:
        if art.is_continuation and stitched:
            target_index = None
            # Prefer a genuine headline match (papers reprint the headline or
            # a shortened form above the continued text).
            head = art.headline.strip().casefold()
            if head:
                for idx in range(len(stitched) - 1, -1, -1):
                    prior = stitched[idx][1].headline.strip().casefold()
                    if prior and (head in prior or prior in head):
                        target_index = idx
                        break
            if target_index is None:
                target_index = len(stitched) - 1

            prior_page, prior_art = stitched[target_index]
            prior_art.body_text = (
                prior_art.body_text.rstrip()
                + f"\n\n[continued on page {page_no}]\n\n"
                + art.body_text.lstrip()
            )
            stitched[target_index] = (prior_page, prior_art)
            continue
        stitched.append((page_no, art))
    return stitched


def _upsert_edition(
    session: Session, pdf: Path, sha: str, doc: PdfDocument
) -> Edition:
    edition = find_edition_by_hash(session, sha)
    if edition is None:
        edition = Edition(
            source_name=doc.source_name,
            edition_date=doc.edition_date,
            pdf_path=str(pdf.resolve()),
            pdf_sha256=sha,
            page_count=doc.page_count,
            status="in_progress",
        )
        session.add(edition)
        session.flush()
    else:
        edition.status = "in_progress"
    return edition


def _resolve_topic_rows(session: Session, specs: list[TopicSpec]) -> dict[str, Topic]:
    """Map casefolded topic name -> Topic row, for validating model output."""
    sync_topics(session, specs)
    rows = session.scalars(select(Topic)).all()
    return {row.name.casefold(): row for row in rows}


def ingest_pdf(
    pdf: Path,
    *,
    force: bool = False,
    max_pages: int | None = None,
    pages: str | None = None,
    skip_pages: str | None = None,
    skip_junk: bool = False,
    include_layout: bool = True,
    progress: ProgressFn = _noop,
) -> IngestReport:
    """Read one newspaper PDF into the database, summarising as it goes.

    ``pages`` / ``skip_pages`` / ``skip_junk`` behave as in prepare_edition.
    Here they also save money: an unread page is a page you are not paying a
    model to segment.
    """
    report = IngestReport(pdf=pdf)
    if not pdf.exists():
        report.errors.append(f"file not found: {pdf}")
        return report

    sha = sha256_file(pdf)
    specs = parse_topics_file()
    limit = CONFIG.max_pages if max_pages is None else max_pages

    progress(f"reading {pdf.name}")
    wanted = resolve_selection(pdf, pages=pages, skip_pages=skip_pages)
    if wanted is not None:
        progress(
            f"page selection: {_compact_pages(sorted(wanted))} "
            f"of {pdf_page_count(pdf)}"
        )
    doc = extract_pdf(
        pdf,
        max_pages=limit,
        pages_wanted=wanted,
        include_layout=include_layout,
    )
    report.skipped_by_selection = doc.skipped_by_selection

    if skip_junk:
        junk = [
            v
            for v in classify_document(doc)
            if v.kind not in {"articles", "no-text"}
        ]
        for verdict in junk:
            progress(f"page {verdict.page_number}: skipped -- {verdict.reason}")
        junk_numbers = {v.page_number for v in junk}
        if junk_numbers:
            doc.pages = [p for p in doc.pages if p.page_number not in junk_numbers]
            report.skipped_as_junk = sorted(junk_numbers)
            report.junk_reasons = {v.page_number: v.reason for v in junk}

    report.pages_seen = len(doc.pages)
    report.pages_with_text = len(doc.text_pages)
    report.image_only_pages = doc.image_only_pages

    with session_scope() as session:
        existing = find_edition_by_hash(session, sha)
        if existing is not None and not force and existing.status == "done":
            report.edition_id = existing.id
            report.skipped_reason = "already ingested (use --force to redo)"
            return report

        edition = _upsert_edition(session, pdf, sha, doc)
        report.edition_id = edition.id
        edition_id = edition.id

        if force:
            stale_ids = list(
                session.scalars(
                    select(Article.id).where(Article.edition_id == edition_id)
                ).all()
            )
            for stale_id in stale_ids:
                # Summaries and topic links go via ON DELETE CASCADE; the FTS
                # table is not a real relation, so clear it explicitly.
                session.execute(
                    text("DELETE FROM articles_fts WHERE article_id = :aid"),
                    {"aid": stale_id},
                )
                session.execute(
                    Article.__table__.delete().where(Article.id == stale_id)
                )
            session.flush()

        already_done_pages = {
            row
            for row in session.scalars(
                select(Article.page_number).where(Article.edition_id == edition_id)
            ).all()
        }

    if not doc.text_pages:
        with session_scope() as session:
            edition = session.get(Edition, report.edition_id)
            if edition is not None:
                edition.status = "no_text_layer"
                edition.note = (
                    "No page in this PDF has an extractable text layer. It is a "
                    "scanned/image e-paper and needs OCR before it can be read."
                )
        report.errors.append(
            "no extractable text on any page -- this PDF is scanned images, "
            "so it needs OCR (see README, 'Scanned PDFs')"
        )
        return report

    # ---- Stage 1: segment each page into articles ------------------------
    collected: list[tuple[int, ExtractedArticle]] = []
    for page in doc.text_pages:
        if page.page_number in already_done_pages and not force:
            progress(f"page {page.page_number}: already segmented, skipping")
            continue
        progress(f"page {page.page_number}: finding articles")
        try:
            seg = segment_page(page.for_prompt(), page.page_number)
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the run
            log.exception("segmentation failed on page %s", page.page_number)
            report.errors.append(f"page {page.page_number} segmentation: {exc}")
            continue
        progress(
            f"page {page.page_number}: {len(seg.articles)} article(s) "
            f"[{seg.page_kind}]"
        )
        collected.extend((page.page_number, art) for art in seg.articles)

    stitched = _stitch_continuations(collected)

    # ---- Stage 2: store articles ----------------------------------------
    new_article_ids: list[int] = []
    with session_scope() as session:
        for page_no, art in stitched:
            body = (art.body_text or "").strip()
            if len(body) < MIN_BODY_CHARS:
                report.articles_skipped_short += 1
                continue
            row = Article(
                edition_id=report.edition_id,
                page_number=page_no,
                headline=(art.headline or "(untitled)").strip(),
                byline=(art.byline or None),
                section=(art.section or None),
                body_text=body,
                word_count=len(body.split()),
            )
            session.add(row)
            session.flush()
            reindex_article(session, row)
            new_article_ids.append(row.id)
        report.articles_found = len(new_article_ids)

    # ---- Stage 3: summarise + tag each article ---------------------------
    pending = _pending_article_ids(report.edition_id)
    for index, article_id in enumerate(pending, start=1):
        try:
            tags = summarise_one(article_id, specs, progress=progress, index=index,
                                 total=len(pending))
        except Exception as exc:  # noqa: BLE001 - keep going through the edition
            log.exception("summarisation failed for article %s", article_id)
            report.errors.append(f"article {article_id} summary: {exc}")
            continue
        report.summaries_written += 1
        report.topic_tags += tags

    with session_scope() as session:
        edition = session.get(Edition, report.edition_id)
        if edition is not None:
            edition.pages_processed = report.pages_with_text
            edition.status = "done" if not report.errors else "done_with_errors"

    return report


def _pending_article_ids(edition_id: int | None) -> list[int]:
    """Articles in this edition that have no summary yet."""
    if edition_id is None:
        return []
    with session_scope() as session:
        return list(
            session.scalars(
                select(Article.id)
                .outerjoin(Summary, Summary.article_id == Article.id)
                .where(Article.edition_id == edition_id, Summary.id.is_(None))
                .order_by(Article.page_number, Article.id)
            ).all()
        )


def summarise_one(
    article_id: int,
    specs: list[TopicSpec] | None = None,
    *,
    progress: ProgressFn = _noop,
    index: int | None = None,
    total: int | None = None,
) -> int:
    """Summarise one stored article and tag it. Returns the tag count."""
    specs = specs if specs is not None else parse_topics_file()

    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise ValueError(f"no article with id {article_id}")
        headline, body, section = article.headline, article.body_text, article.section

    counter = ""
    if index is not None and total is not None:
        counter = f"[{index}/{total}] "
    progress(f"{counter}summarising: {headline[:70]}")

    hints = keyword_matches(f"{headline}\n{body}", specs)
    result = summarise_article(
        headline=headline,
        body_text=body,
        specs=specs,
        section=section,
        keyword_hint=hints or None,
    )

    tag_count = 0
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise ValueError(f"no article with id {article_id}")

        known = _resolve_topic_rows(session, specs)

        if article.summary is not None:
            session.delete(article.summary)
            session.flush()

        session.add(
            Summary(
                article_id=article.id,
                one_liner=result.one_liner.strip(),
                bullets_json=json.dumps([b.strip() for b in result.bullets if b.strip()]),
                entities_json=json.dumps(result.entities[:8]),
                why_it_matters=(result.why_it_matters or None),
                category=result.category,
                read_minutes=max(1, result.read_minutes),
                model=CONFIG.model,
            )
        )

        session.execute(
            ArticleTopic.__table__.delete().where(
                ArticleTopic.article_id == article.id
            )
        )

        seen: set[int] = set()
        for tag in result.topics:
            row = known.get(tag.topic.strip().casefold())
            if row is None:
                log.warning(
                    "model returned unknown topic %r for article %s -- dropped",
                    tag.topic,
                    article.id,
                )
                continue
            if tag.confidence < 0.4 or row.id in seen:
                continue
            seen.add(row.id)
            matched_by = "llm+keyword" if row.name in hints else "llm"
            session.add(
                ArticleTopic(
                    article_id=article.id,
                    topic_id=row.id,
                    confidence=round(float(tag.confidence), 3),
                    matched_by=matched_by,
                    rationale=tag.rationale.strip() or None,
                )
            )
            tag_count += 1

        session.flush()
        session.refresh(article)
        reindex_article(session, article)

    return tag_count


# =========================================================================== #
# Manual (no-API) workflow
# =========================================================================== #


@dataclass
class PrepareReport:
    pdf: Path
    edition_id: int | None = None
    source_name: str | None = None
    edition_date: str | None = None
    pages_seen: int = 0
    pages_with_text: int = 0
    image_only_pages: list[int] = field(default_factory=list)
    prompt_dir: Path | None = None
    prompt_files: list[Path] = field(default_factory=list)
    skipped_by_selection: list[int] = field(default_factory=list)
    skipped_as_junk: list[int] = field(default_factory=list)
    junk_reasons: dict[int, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_lines(self) -> list[str]:
        lines = [
            f"{self.pdf.name}",
            f"  edition         #{self.edition_id}  {self.source_name or '?'}"
            f"  {self.edition_date or ''}".rstrip(),
            f"  pages           {self.pages_with_text} with text / {self.pages_seen} read",
            f"  prompts written {len(self.prompt_files)}",
        ]
        if self.skipped_by_selection:
            lines.append(
                f"  not selected    pages "
                f"{_compact_pages(self.skipped_by_selection)}"
            )
        if self.skipped_as_junk:
            lines.append(
                f"  skipped as junk pages {_compact_pages(self.skipped_as_junk)}"
            )
            for page_no in self.skipped_as_junk[:6]:
                reason = self.junk_reasons.get(page_no, "")
                lines.append(f"                    p{page_no}: {reason}")
        if self.prompt_dir:
            lines.append(f"  folder          {self.prompt_dir}")
        if self.image_only_pages:
            preview = ", ".join(str(p) for p in self.image_only_pages[:12])
            more = " ..." if len(self.image_only_pages) > 12 else ""
            lines.append(f"  NO TEXT LAYER   pages {preview}{more}  (needs OCR)")
        for err in self.errors:
            lines.append(f"  ERROR           {err}")
        return lines


@dataclass
class LoadReport:
    source: str
    edition_id: int | None = None
    articles_added: int = 0
    articles_updated: int = 0
    continuations_merged: int = 0
    topic_tags: int = 0
    bodies_located: int = 0
    bodies_unmatched: int = 0
    unknown_topics: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    no_articles_reported: bool = False

    def as_lines(self) -> list[str]:
        if self.no_articles_reported:
            return [
                f"{self.source}",
                "  no articles     the model reported this page as adverts, "
                "classifieds or tables",
            ]
        lines = [
            f"{self.source}",
            f"  articles added  {self.articles_added}",
        ]
        if self.articles_updated:
            lines.append(f"  articles updated {self.articles_updated}")
        if self.continuations_merged:
            lines.append(f"  continuations merged {self.continuations_merged}")
        lines.append(f"  topic tags      {self.topic_tags}")
        lines.append(
            f"  full text       {self.bodies_located} located"
            + (
                f", {self.bodies_unmatched} not found"
                if self.bodies_unmatched
                else ""
            )
        )
        if self.unknown_topics:
            names = ", ".join(sorted(set(self.unknown_topics))[:6])
            lines.append(f"  topics dropped  {names}  (not in topics.txt)")
        for warning in self.warnings[:8]:
            lines.append(f"  note            {warning}")
        for err in self.errors:
            lines.append(f"  ERROR           {err}")
        return lines


def prepare_edition(
    pdf: Path,
    *,
    max_pages: int | None = None,
    max_chars: int | None = None,
    pages_per_chunk: int | None = None,
    include_layout: bool = False,
    pages: str | None = None,
    skip_pages: str | None = None,
    skip_junk: bool = False,
    progress: ProgressFn = _noop,
) -> PrepareReport:
    """Extract a PDF and write chat prompts for it. Makes no API calls.

    Everything here is local: the edition and its page text go into the
    database, and the prompts land in a folder ready to paste into a chat
    window one at a time.

    ``pages`` / ``skip_pages`` take selectors like "1-4,7,10-12"; unselected
    pages are never read. ``skip_junk`` additionally drops pages the
    classifier judges to be advertising, classifieds or data tables.
    """
    report = PrepareReport(pdf=pdf)
    if not pdf.exists():
        report.errors.append(f"file not found: {pdf}")
        return report

    progress(f"reading {pdf.name}")
    sha = sha256_file(pdf)
    limit = CONFIG.max_pages if max_pages is None else max_pages

    wanted = resolve_selection(pdf, pages=pages, skip_pages=skip_pages)
    if wanted is not None:
        progress(
            f"page selection: {_compact_pages(sorted(wanted))} "
            f"of {pdf_page_count(pdf)}"
        )
    doc = extract_pdf(
        pdf,
        max_pages=limit,
        pages_wanted=wanted,
        include_layout=include_layout,
    )
    report.skipped_by_selection = doc.skipped_by_selection

    # Whether the PDF had readable text *before* any filtering, so that
    # "everything was filtered out" is never reported as "needs OCR".
    had_text_before_filtering = bool(doc.text_pages)

    if skip_junk:
        verdicts = classify_document(doc)
        junk = [v for v in verdicts if v.kind not in {"articles", "no-text"}]
        for verdict in junk:
            progress(f"page {verdict.page_number}: skipped -- {verdict.reason}")
        junk_numbers = {v.page_number for v in junk}
        if junk_numbers:
            doc.pages = [p for p in doc.pages if p.page_number not in junk_numbers]
            doc.skipped_as_junk = sorted(junk_numbers)
            report.skipped_as_junk = doc.skipped_as_junk
            report.junk_reasons = {v.page_number: v.reason for v in junk}

    report.pages_seen = len(doc.pages)
    report.pages_with_text = len(doc.text_pages)
    report.image_only_pages = doc.image_only_pages
    report.source_name = doc.source_name
    report.edition_date = doc.edition_date.isoformat() if doc.edition_date else None

    with session_scope() as session:
        edition = _upsert_edition(session, pdf, sha, doc)
        edition.status = "awaiting_summaries"
        report.edition_id = edition.id

        # Store page text, replacing any earlier extraction of the same page.
        for page in doc.pages:
            existing = session.scalar(
                select(Page).where(
                    Page.edition_id == edition.id, Page.page_number == page.page_number
                )
            )
            if existing is None:
                session.add(
                    Page(
                        edition_id=edition.id,
                        page_number=page.page_number,
                        column_text=page.column_text,
                        layout_text=page.layout_text,
                        has_text_layer=int(page.has_text_layer),
                    )
                )
            else:
                existing.column_text = page.column_text
                existing.layout_text = page.layout_text
                existing.has_text_layer = int(page.has_text_layer)

        sync_topics(session, parse_topics_file())

    if not doc.text_pages:
        # Two very different causes, and conflating them sends you chasing an
        # OCR problem you do not have.
        filtered_out = had_text_before_filtering
        with session_scope() as session:
            edition = session.get(Edition, report.edition_id)
            if edition is not None:
                edition.status = (
                    "all_pages_filtered" if filtered_out else "no_text_layer"
                )
                edition.note = (
                    "Every page that was selected got dropped by --skip-junk."
                    if filtered_out
                    else "No page in this PDF has an extractable text layer. "
                    "It is a scanned/image e-paper and needs OCR before it "
                    "can be read."
                )
        if filtered_out:
            report.errors.append(
                "every selected page was dropped as junk, so there is nothing "
                "to summarise. Run 'newsagent pages' on this PDF to see how "
                "each page was judged, then name the pages you want with "
                "--pages"
            )
        else:
            report.errors.append(
                "no extractable text on any page -- this PDF is scanned "
                "images, so it needs OCR (see README, 'Scanned PDFs')"
            )
        return report

    chunks = build_prompts(
        [
            (p.page_number, p.for_prompt(include_layout=include_layout))
            for p in doc.text_pages
        ],
        parse_topics_file(),
        max_chars=max_chars or DEFAULT_CHUNK_CHARS,
        pages_per_chunk=pages_per_chunk,
    )

    out_dir = CONFIG.prompts_dir / f"edition-{report.edition_id:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("chunk-*.txt"):
        stale.unlink()

    for chunk in chunks:
        target = out_dir / chunk.filename
        target.write_text(chunk.text, encoding="utf-8")
        report.prompt_files.append(target)
        progress(
            f"wrote {chunk.filename} (pages "
            f"{chunk.page_numbers[0]}-{chunk.page_numbers[-1]})"
        )

    (out_dir / "README.txt").write_text(
        _prompt_folder_readme(report, chunks), encoding="utf-8"
    )
    report.prompt_dir = out_dir
    return report


def _prompt_folder_readme(report: PrepareReport, chunks: list[PromptChunk]) -> str:
    listing = "\n".join(
        f"  {c.filename}   pages {c.page_numbers[0]}-{c.page_numbers[-1]}"
        for c in chunks
    )
    return f"""\
Prompts for edition #{report.edition_id} -- {report.pdf.name}
{report.source_name or ''} {report.edition_date or ''}

{len(chunks)} prompt file(s):
{listing}

WHAT TO DO
1. Open chunk-01.txt, copy all of it, paste it into your LLM chat window.
2. Copy the whole reply and save it as a text file, e.g. reply-01.txt,
   in this folder.
3. Repeat for each chunk.
4. Load them all at once:

     python -m newsagent load "{report.prompt_dir or '.'}" --edition {report.edition_id}

   'load' reads every reply-*.txt / response-*.txt / *.md in the folder and
   ignores the chunk-*.txt prompts, so you can leave everything here.

You can also load one at a time as you go, or pipe from the clipboard:

     python -m newsagent load reply-01.txt --edition {report.edition_id}

Loading the same reply twice is safe -- articles are matched on page and
headline and updated in place rather than duplicated.
"""


def _infer_page(
    anchor: str, page_texts: dict[int, str]
) -> int | None:
    """Work out which page an article came from, when PAGE was omitted."""
    from .manual import _find_anchor, _normalise_with_map

    if not anchor:
        return None
    for page_no in sorted(page_texts):
        norm, _ = _normalise_with_map(page_texts[page_no])
        if _find_anchor(norm, anchor, 0) is not None:
            return page_no
    return None


def load_response(
    raw: str,
    *,
    edition_id: int | None = None,
    source: str = "pasted response",
    progress: ProgressFn = _noop,
) -> LoadReport:
    """Parse a pasted chat reply and write its articles into the database."""
    report = LoadReport(source=source)

    parsed: ParseResult = parse_response(raw)
    report.warnings.extend(parsed.warnings)

    if not parsed.articles:
        if parsed.format_seen == "no-articles":
            # The model reported the page as all adverts/classifieds/tables.
            # That is the right answer, not a failure.
            report.no_articles_reported = True
            progress(f"{source}: reported as having no articles -- nothing to load")
            return report
        report.errors.append("no usable articles in this response")
        return report

    progress(
        f"parsed {len(parsed.articles)} article(s) from {source} "
        f"({parsed.format_seen} format)"
    )

    specs = parse_topics_file()

    with session_scope() as session:
        if edition_id is None:
            edition = session.scalar(
                select(Edition).order_by(Edition.ingested_at.desc()).limit(1)
            )
            if edition is None:
                report.errors.append(
                    "no editions in the database -- run 'newsagent prompt "
                    "<pdf>' first"
                )
                return report
        else:
            edition = session.get(Edition, edition_id)
            if edition is None:
                report.errors.append(f"no edition with id {edition_id}")
                return report
        report.edition_id = edition.id

        page_texts = {
            page.page_number: page.column_text
            for page in session.scalars(
                select(Page).where(Page.edition_id == edition.id)
            ).all()
        }

    if not page_texts:
        report.errors.append(
            f"edition #{report.edition_id} has no stored page text -- re-run "
            f"'newsagent prompt' for it"
        )
        return report

    # Group by page so bodies can be sliced page by page, in reading order.
    by_page: dict[int, list[ParsedArticle]] = {}
    for article in parsed.articles:
        page_no = article.page
        if page_no not in page_texts:
            inferred = _infer_page(article.anchor, page_texts)
            if inferred is not None:
                if page_no is not None:
                    report.warnings.append(
                        f"{article.headline[:50]!r} said page {page_no} but its "
                        f"text is on page {inferred} -- using {inferred}"
                    )
                page_no = inferred
        if page_no is None:
            report.warnings.append(
                f"could not place {article.headline[:50]!r} on any page -- "
                f"stored without full text"
            )
            page_no = 0
        by_page.setdefault(page_no, []).append(article)

    with session_scope() as session:
        known = _resolve_topic_rows(session, specs)

        for page_no in sorted(by_page):
            articles = by_page[page_no]
            page_text = page_texts.get(page_no, "")
            sliced = (
                slice_bodies(page_text, articles)
                if page_text
                else [(a, "", "unmatched") for a in articles]
            )

            for article, body, body_source in sliced:
                if body_source == "anchor":
                    report.bodies_located += 1
                else:
                    report.bodies_unmatched += 1

                # A story continued from an earlier page joins the original
                # rather than becoming a second article.
                if article.continued:
                    prior = _find_continuation_target(
                        session, edition_id=report.edition_id, headline=article.headline
                    )
                    if prior is not None and body:
                        prior.body_text = (
                            prior.body_text.rstrip()
                            + f"\n\n[continued on page {page_no}]\n\n"
                            + body.lstrip()
                        )
                        prior.word_count = len(prior.body_text.split())
                        session.flush()
                        reindex_article(session, prior)
                        report.continuations_merged += 1
                        continue

                row = session.scalar(
                    select(Article).where(
                        Article.edition_id == report.edition_id,
                        Article.page_number == page_no,
                        Article.headline == article.headline.strip(),
                    )
                )
                is_new = row is None
                if row is None:
                    row = Article(
                        edition_id=report.edition_id,
                        page_number=page_no,
                        headline=article.headline.strip(),
                    )
                    session.add(row)

                row.byline = article.byline
                row.section = article.section
                row.body_text = body
                row.word_count = len(body.split())
                row.body_source = body_source
                session.flush()

                if row.summary is not None:
                    session.delete(row.summary)
                    session.flush()

                session.add(
                    Summary(
                        article_id=row.id,
                        one_liner=article.summary,
                        bullets_json=json.dumps(article.bullets),
                        entities_json=json.dumps(article.entities[:8]),
                        why_it_matters=article.why,
                        category=article.category,
                        read_minutes=article.read_minutes,
                        model="chat (manual paste)",
                    )
                )

                session.execute(
                    ArticleTopic.__table__.delete().where(
                        ArticleTopic.article_id == row.id
                    )
                )

                hints = keyword_matches(f"{row.headline}\n{body}", specs)
                seen: set[int] = set()
                for name, confidence, rationale in article.topics:
                    topic_row = known.get(name.strip().casefold())
                    if topic_row is None:
                        report.unknown_topics.append(name.strip())
                        continue
                    if confidence < 0.4 or topic_row.id in seen:
                        continue
                    seen.add(topic_row.id)
                    session.add(
                        ArticleTopic(
                            article_id=row.id,
                            topic_id=topic_row.id,
                            confidence=round(confidence, 3),
                            matched_by=(
                                "chat+keyword" if topic_row.name in hints else "chat"
                            ),
                            rationale=rationale or None,
                        )
                    )
                    report.topic_tags += 1

                session.flush()
                session.refresh(row)
                reindex_article(session, row)

                if is_new:
                    report.articles_added += 1
                else:
                    report.articles_updated += 1

        edition = session.get(Edition, report.edition_id)
        if edition is not None:
            edition.status = "done"
            edition.pages_processed = len(
                {p for p in by_page if p in page_texts}
            ) or edition.pages_processed

    return report


def _find_continuation_target(
    session: Session, *, edition_id: int | None, headline: str
) -> Article | None:
    """Find the article an explicitly-continued fragment belongs to."""
    if edition_id is None:
        return None
    head = headline.strip().casefold()
    if not head:
        return None
    candidates = session.scalars(
        select(Article)
        .where(Article.edition_id == edition_id)
        .order_by(Article.page_number, Article.id)
    ).all()
    for candidate in candidates:
        other = candidate.headline.strip().casefold()
        if other and (head in other or other in head):
            return candidate
    return None


def load_response_files(
    paths: list[Path],
    *,
    edition_id: int | None = None,
    progress: ProgressFn = _noop,
) -> list[LoadReport]:
    """Load one or more saved chat replies."""
    reports: list[LoadReport] = []
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report = LoadReport(source=path.name)
            report.errors.append(f"could not read: {exc}")
            reports.append(report)
            continue
        reports.append(
            load_response(
                raw, edition_id=edition_id, source=path.name, progress=progress
            )
        )
    return reports


# Files in a prompt folder that are prompts, not replies.
_PROMPT_GLOBS = ("chunk-*.txt", "README.txt")


def find_response_files(folder: Path) -> list[Path]:
    """Pick the reply files out of a prompt folder, ignoring the prompts."""
    prompts = {p.name for glob in _PROMPT_GLOBS for p in folder.glob(glob)}
    candidates: list[Path] = []
    for pattern in ("*.txt", "*.md", "*.json"):
        for path in sorted(folder.glob(pattern)):
            if path.name not in prompts:
                candidates.append(path)
    return candidates


def ingest_inbox(
    *, force: bool = False, progress: ProgressFn = _noop
) -> list[IngestReport]:
    """Ingest every PDF sitting in the inbox folder."""
    pdfs = sorted(CONFIG.inbox.glob("*.pdf"))
    if not pdfs:
        progress(f"no PDFs found in {CONFIG.inbox}")
        return []
    return [ingest_pdf(p, force=force, progress=progress) for p in pdfs]
