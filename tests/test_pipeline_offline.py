"""End-to-end test of the pipeline with the two LLM calls stubbed out.

This exercises extraction, segmentation handling, continuation stitching,
storage, topic validation, FTS indexing, search, and the digest -- everything
except the model calls themselves. It needs no API key and costs nothing, so
it is the fast way to check a change did not break the plumbing.

    python tests/test_pipeline_offline.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Point at a throwaway database before newsagent.config is imported.
_TMP = tempfile.mkdtemp(prefix="newsagent-test-")
os.environ["NEWSAGENT_DB"] = str(Path(_TMP) / "test.db")
os.environ["NEWSAGENT_TOPICS_FILE"] = str(ROOT / "topics.txt")

from newsagent import pipeline, queries  # noqa: E402
from newsagent.extract import extract_pdf  # noqa: E402
from stubs import fake_segment_page, fake_summarise_article  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    pdf = ROOT / "inbox" / "sample-edition.pdf"
    if not pdf.exists():
        print("generating sample PDF first")
        sys.path.insert(0, str(ROOT / "scripts"))
        from make_sample_pdf import build

        build(pdf)

    print("\n[1] extraction")
    # Pages 1-3 carry articles; 4-6 are classifieds, market data and a
    # full-page advert, which the page selector exists to leave out.
    doc = extract_pdf(pdf, pages_wanted={1, 2, 3})
    check("sample pdf has 6 pages", doc.page_count == 6, f"got {doc.page_count}")
    check("reads just the 3 selected pages", len(doc.pages) == 3,
          f"got {len(doc.pages)}")
    check("every selected page has a text layer", not doc.image_only_pages,
          str(doc.image_only_pages))
    check("source name is the pdf filename", doc.source_name == pdf.stem, repr(doc.source_name))
    check("masthead date detected", str(doc.edition_date) == "2026-03-12", str(doc.edition_date))
    check(
        "columns read in order (headline intact)",
        "RBI holds repo rate at" in doc.pages[0].column_text,
    )

    print("\n[2] ingest with stubbed LLM")
    pipeline.segment_page = fake_segment_page
    pipeline.summarise_article = fake_summarise_article
    report = pipeline.ingest_pdf(pdf, force=True, pages="1-3")
    check("no ingest errors", not report.errors, str(report.errors))
    check("found articles", report.articles_found >= 6, f"got {report.articles_found}")
    check(
        "summarised every article",
        report.summaries_written == report.articles_found,
        f"{report.summaries_written} of {report.articles_found}",
    )
    check("applied topic tags", report.topic_tags > 0, str(report.topic_tags))

    print("\n[3] topic validation")
    topics = {t["topic"] for t in queries.list_topics()}
    check(
        "hallucinated topic rejected",
        "Topic That Does Not Exist" not in topics,
    )
    tagged = queries.articles_by_topic("Indian Economy", limit=10)
    check("Indian Economy tagged", len(tagged) >= 1, f"got {len(tagged)}")
    ai_tagged = queries.articles_by_topic("Artificial Intelligence", limit=10)
    check("Artificial Intelligence tagged", len(ai_tagged) >= 1, f"got {len(ai_tagged)}")

    print("\n[4] full-text search")
    hits = queries.search_articles("repo rate")
    check("FTS finds 'repo rate'", len(hits) >= 1, f"got {len(hits)}")
    check(
        "search result carries its summary",
        bool(hits and hits[0]["summary"] and hits[0]["summary"]["one_liner"]),
    )
    check("punctuation does not break FTS", isinstance(queries.search_articles('"a-b" (c)*'), list))
    check("empty query returns nothing", queries.search_articles("   ") == [])
    check(
        "unmatched query returns nothing",
        queries.search_articles("zzzzunlikelyzzz") == [],
    )

    print("\n[5] digest")
    data = queries.digest(since=None, topics_only=True)
    check("digest groups by topic", len(data["by_topic"]) >= 2, str(list(data["by_topic"])))
    check(
        "digest counts match",
        data["articles_considered"] >= report.articles_found,
        str(data["articles_considered"]),
    )
    everything = queries.digest(since=None, topics_only=False)
    check("digest --all includes untagged bucket", "untagged" in everything)

    print("\n[6] idempotency + resume")
    again = pipeline.ingest_pdf(pdf, force=False, pages="1-3")
    check("re-ingest is a no-op", again.skipped_reason is not None, str(again.skipped_reason))
    before = queries.stats()["articles"]
    pipeline.ingest_pdf(pdf, force=False, pages="1-3")
    check("no duplicate articles", queries.stats()["articles"] == before)

    print("\n[7] force re-ingest cleans up")
    redo = pipeline.ingest_pdf(pdf, force=True, pages="1-3")
    check(
        "force does not duplicate",
        queries.stats()["articles"] == redo.articles_found,
        f"{queries.stats()['articles']} vs {redo.articles_found}",
    )
    fts_hits = queries.search_articles("repo rate")
    check("FTS has no stale duplicates", len(fts_hits) == 1, f"got {len(fts_hits)}")

    print("\n[8] stats + editions")
    stats = queries.stats()
    check("stats reports summaries", stats["summaries"] == stats["articles"])
    check("awaiting_summary is zero", stats["awaiting_summary"] == 0, str(stats["awaiting_summary"]))
    eds = queries.list_editions()
    check("one edition listed", len(eds) == 1, f"got {len(eds)}")
    check("edition marked done", eds[0]["status"] == "done", eds[0]["status"])

    print(f"\n{'=' * 60}")
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
    print(f"{'=' * 60}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
