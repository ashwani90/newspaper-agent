"""Tests for the no-API chat workflow: prompt building, response parsing,
body anchoring, and the load round trip.

Needs no API key and costs nothing.

    python tests/test_manual.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = tempfile.mkdtemp(prefix="newsagent-manual-")
# Isolate this run in its own Postgres database (dropped and recreated
# below), before webapp.config/newsagent.config are imported.
os.environ["POSTGRES_DB"] = os.getenv(
    "NEWSAGENT_TEST_POSTGRES_DB", "newspaper_agent_test"
)
os.environ["NEWSAGENT_TOPICS_FILE"] = str(ROOT / "topics.txt")
os.environ["NEWSAGENT_PROMPTS"] = str(Path(_TMP) / "prompts")

from webapp.database import Base, get_engine  # noqa: E402
from webapp import models  # noqa: E402,F401 (registers model classes on Base)

try:
    get_engine().connect().close()
except Exception as exc:  # noqa: BLE001
    print(f"SKIP: no reachable test Postgres database ({exc})")
    sys.exit(0)

Base.metadata.drop_all(get_engine())

from newsagent import pipeline, queries  # noqa: E402
from newsagent.manual import (  # noqa: E402
    ParsedArticle,
    build_prompts,
    parse_response,
    slice_bodies,
)
from newsagent.topics import parse_topics_file  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}")


# A page with three articles, laid out the way extraction renders one:
# section label, wrapped headline, byline, then body.
PAGE = """\
THE DAILY EXAMPLE
New Delhi | Tuesday, 12 March 2026
FRONT PAGE
Rates held steady as
prices cool
By A Reporter
The central bank kept its benchmark rate
unchanged on Monday, ending a run of
three consecutive cuts.
Officials said the pause buys time.
BUSINESS
Startup raises big round
By B Reporter
A lending startup has raised eighty five
million dollars in a new funding round.
The money funds expansion.
WORLD
Talks stall in Geneva
By C Reporter
Negotiations collapsed without agreement
on Friday, with both sides blaming
the other.
"""

MESSY_REPLY = """\
Sure! Here are the articles I found on that page:

```text
### ARTICLE
PAGE: 1
**HEADLINE:** Rates held steady as prices cool
BYLINE: By A Reporter
SECTION: FRONT PAGE
CATEGORY: Economy
ANCHOR: The central bank kept its benchmark rate unchanged on Monday
READ_MINUTES: 2
CONTINUED: no
SUMMARY: The central bank held its benchmark rate after three consecutive cuts.
- BULLET: Officials said the pause buys time.
- BULLET: It ends a run of three cuts.
ENTITIES: Central Bank, New Delhi
WHY: A pause lets earlier cuts work through the economy.
TOPIC: Indian Economy | 97% | Directly about central bank rate policy
TOPIC: Nonexistent Topic | 0.9 | should be discarded
### END

### ARTICLE
PAGE: 1
HEADLINE: Startup raises big round
SECTION: BUSINESS
CATEGORY: Business
ANCHOR: A lending startup has raised eighty five million dollars
SUMMARY: A lending startup raised $85 million to fund expansion.
BULLET: The round funds expansion.
TOPIC: Startups & Funding | 0.95 | A funding round and valuation
### END

### ARTICLE
PAGE: 1
HEADLINE: Talks stall in Geneva
SECTION: WORLD
CATEGORY: World
ANCHOR: Negotiations collapsed without agreement on Friday
SUMMARY: Talks collapsed in Geneva with both sides blaming the other.
BULLET: No agreement was reached.
TOPIC: Geopolitics | 0.9 | Stalled international negotiations
### END
```

Let me know if you want more detail on any of these.
"""

JSON_REPLY = """\
Here you go:

```json
{"articles": [
  {"page": 1, "headline": "Rates held steady as prices cool",
   "section": "FRONT PAGE", "category": "Economy",
   "anchor": "The central bank kept its benchmark rate unchanged on Monday",
   "summary": "The central bank held its rate steady.",
   "bullets": ["Ends three cuts.", "Pause buys time."],
   "entities": ["Central Bank"],
   "why_it_matters": "Signals a wait-and-see stance.",
   "read_minutes": 2,
   "topics": [{"topic": "Indian Economy", "confidence": 0.95,
               "rationale": "Rate policy"}]}
]}
```
"""

TRUNCATED_REPLY = """\
### ARTICLE
PAGE: 1
HEADLINE: Rates held steady as prices cool
ANCHOR: The central bank kept its benchmark rate unchanged on Monday
SUMMARY: The central bank held its benchmark rate.
BULLET: Ends three cuts.
### END

### ARTICLE
PAGE: 1
HEADLINE: Startup raises big
"""


def main() -> int:
    print("\n[1] prompt building")
    specs = parse_topics_file()
    pages = [(1, "A" * 10_000), (2, "B" * 10_000), (3, "C" * 10_000)]

    chunks = build_prompts(pages, specs, max_chars=25_000)
    check("packs by character budget", len(chunks) == 2, f"got {len(chunks)}")
    check("first chunk holds pages 1-2", chunks[0].page_numbers == [1, 2],
          str(chunks[0].page_numbers))
    check("second chunk holds page 3", chunks[1].page_numbers == [3],
          str(chunks[1].page_numbers))
    check("chunk numbering is 1-based", chunks[0].index == 1 and chunks[0].total == 2)
    check("filenames are zero-padded", chunks[0].filename == "chunk-01.txt",
          chunks[0].filename)

    fixed = build_prompts(pages, specs, pages_per_chunk=1)
    check("pages_per_chunk overrides budget", len(fixed) == 3, f"got {len(fixed)}")

    oversized = build_prompts([(1, "X" * 90_000)], specs, max_chars=10_000)
    check("one oversized page still gets a chunk", len(oversized) == 1)

    check("prompt carries the output format", "### ARTICLE" in chunks[0].text)
    check("prompt carries the topic list", "Indian Economy" in chunks[0].text)
    check("prompt carries the page text", "AAAA" in chunks[0].text)
    check("empty page list yields no chunks", build_prompts([], specs) == [])

    print("\n[2] parsing a messy chat reply")
    result = parse_response(MESSY_REPLY)
    check("found all 3 articles", len(result.articles) == 3, f"got {len(result.articles)}")
    check("format detected as blocks", result.format_seen == "blocks")

    first = result.articles[0]
    check("strips markdown bold from keys",
          first.headline == "Rates held steady as prices cool", repr(first.headline))
    check("reads byline", first.byline == "By A Reporter", repr(first.byline))
    check("reads section", first.section == "FRONT PAGE", repr(first.section))
    check("ignores preamble and code fences", first.page == 1, str(first.page))
    check("strips list markers from BULLET lines", len(first.bullets) == 2,
          str(first.bullets))
    check("splits ENTITIES on commas", first.entities == ["Central Bank", "New Delhi"],
          str(first.entities))
    check("reads WHY", bool(first.why), repr(first.why))
    check("percentage confidence becomes a fraction",
          abs(first.topics[0][1] - 0.97) < 1e-6, str(first.topics[0]))
    check("keeps both topics for validation later", len(first.topics) == 2,
          str(len(first.topics)))
    check("read_minutes parsed", first.read_minutes == 2, str(first.read_minutes))
    check("CONTINUED: no is false", first.continued is False)
    check("missing optional fields default cleanly",
          result.articles[1].byline is None and result.articles[1].read_minutes == 1)

    print("\n[3] parsing a JSON reply")
    js = parse_response(JSON_REPLY)
    check("JSON path used", js.format_seen == "json", js.format_seen)
    check("JSON article parsed", len(js.articles) == 1, f"got {len(js.articles)}")
    check("JSON bullets parsed", len(js.articles[0].bullets) == 2)
    check("JSON topics parsed", js.articles[0].topics[0][0] == "Indian Economy",
          str(js.articles[0].topics))
    check("JSON why_it_matters mapped", bool(js.articles[0].why))

    print("\n[4] degraded input")
    trunc = parse_response(TRUNCATED_REPLY)
    check("truncated reply keeps the complete article", len(trunc.articles) == 1,
          f"got {len(trunc.articles)}")
    empty = parse_response("")
    check("empty reply yields no articles and a warning",
          not empty.articles and bool(empty.warnings))
    junk = parse_response("I could not find any articles on this page, sorry.")
    check("prose-only reply warns clearly",
          not junk.articles and any("no article blocks" in w for w in junk.warnings),
          str(junk.warnings))
    bad_json = parse_response('{"articles": [{"headline": "x", "summ')
    check("unparseable JSON warns about truncation",
          any("cut off" in w or "did not parse" in w for w in bad_json.warnings),
          str(bad_json.warnings))
    no_anchor = parse_response(
        "### ARTICLE\nHEADLINE: No anchor here\nSUMMARY: A summary.\n### END"
    )
    check("missing ANCHOR is warned about",
          any("no ANCHOR" in w for w in no_anchor.warnings), str(no_anchor.warnings))

    print("\n[5] body anchoring")
    articles = parse_response(MESSY_REPLY).articles
    sliced = slice_bodies(PAGE, articles)
    check("every body located", all(src == "anchor" for _, _, src in sliced),
          str([s for _, _, s in sliced]))

    body1 = sliced[0][1]
    check("body starts at the anchor", body1.startswith("The central bank kept"),
          body1[:40])
    check("body ends before the next section label", "BUSINESS" not in body1, body1[-60:])
    check("body ends before the next headline",
          "Startup raises big round" not in body1)
    check("body keeps its own full text", "buys time" in body1)

    body2 = sliced[1][1]
    check("middle body excludes neighbours",
          "WORLD" not in body2 and "central bank" not in body2, body2)
    check("last body runs to end of page", "blaming" in sliced[2][1])

    lost = slice_bodies(PAGE, [ParsedArticle(headline="X", anchor="not on this page at all")])
    check("unfindable anchor reports unmatched", lost[0][2] == "unmatched", str(lost[0]))

    reordered = slice_bodies(
        PAGE,
        [
            ParsedArticle(headline="Talks stall in Geneva",
                          anchor="Negotiations collapsed without agreement on Friday"),
            ParsedArticle(headline="Rates held steady as prices cool",
                          anchor="The central bank kept its benchmark rate unchanged"),
        ],
    )
    check("out-of-order articles still anchor",
          all(src == "anchor" for _, _, src in reordered),
          str([s for _, _, s in reordered]))

    print("\n[6] load round trip")
    pdf = ROOT / "inbox" / "sample-edition.pdf"
    if not pdf.exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        from make_sample_pdf import build

        build(pdf)

    prep = pipeline.prepare_edition(pdf)
    check("prepare makes no API call and succeeds", not prep.errors, str(prep.errors))
    check("prepare stored an edition", prep.edition_id is not None)
    check("prepare wrote prompt files", len(prep.prompt_files) >= 1,
          str(len(prep.prompt_files)))
    check("prompt folder has a README",
          (prep.prompt_dir / "README.txt").exists() if prep.prompt_dir else False)

    # A deliberately messy fixture: preamble, markdown bold, a code fence,
    # list markers, a percentage confidence and an invented topic.
    reply = ROOT / "tests" / "fixtures" / "messy-reply.txt"
    if not reply.exists():
        check("messy reply fixture present", False, f"missing {reply}")
    else:
        report = pipeline.load_response(
            reply.read_text(encoding="utf-8"),
            edition_id=prep.edition_id,
            source="messy-reply.txt",
        )
        check("load found no errors", not report.errors, str(report.errors))
        check("load added 8 articles", report.articles_added == 8,
              str(report.articles_added))
        check("all bodies located from the page text", report.bodies_unmatched == 0,
              str(report.bodies_unmatched))
        check("invented topic discarded", "Cricket" in report.unknown_topics,
              str(report.unknown_topics))
        check("topic tags written", report.topic_tags >= 8, str(report.topic_tags))

        again = pipeline.load_response(
            reply.read_text(encoding="utf-8"),
            edition_id=prep.edition_id,
            source="messy-reply.txt",
        )
        check("re-loading updates instead of duplicating",
              again.articles_added == 0 and again.articles_updated == 8,
              f"added {again.articles_added}, updated {again.articles_updated}")
        check("no duplicate rows after re-load", queries.stats()["articles"] == 8,
              str(queries.stats()["articles"]))

        hits = queries.search_articles("repo rate")
        check("loaded articles are searchable", len(hits) == 1, f"got {len(hits)}")
        check("search result has the chat summary",
              bool(hits and hits[0]["summary"]["one_liner"]))
        check("summary records its provenance",
              queries.get_article(1)["summary"] is not None)

        data = queries.digest(since=None, topics_only=True)
        check("digest built from loaded data", len(data["by_topic"]) >= 5,
              str(list(data["by_topic"])))
        check("matched count never exceeds considered",
              data["matched_your_topics"] <= data["articles_considered"],
              f"{data['matched_your_topics']} of {data['articles_considered']}")

    print("\n[7] page selection")
    from newsagent.extract import (
        PageSpecError,
        classify_document,
        extract_pdf,
        parse_page_spec,
        pdf_page_count,
        resolve_selection,
    )
    from newsagent.pipeline import _compact_pages

    check("single page", parse_page_spec("5", 12) == {5})
    check("range", parse_page_spec("1-4", 12) == {1, 2, 3, 4})
    check("mixed list", parse_page_spec("1-4,7,10-12", 12) == {1, 2, 3, 4, 7, 10, 11, 12})
    check("space separated", parse_page_spec("1 3 5", 12) == {1, 3, 5})
    check("open start", parse_page_spec("-3", 12) == {1, 2, 3})
    check("open end", parse_page_spec("10-", 12) == {10, 11, 12})
    check("odd", parse_page_spec("odd", 6) == {1, 3, 5})
    check("even", parse_page_spec("even", 6) == {2, 4, 6})
    check("empty spec", parse_page_spec("", 12) == set())

    for bad, why in [
        ("abc", "gibberish"),
        ("5-2", "backwards range"),
        ("0", "page zero"),
        ("99", "beyond the last page"),
    ]:
        try:
            parse_page_spec(bad, 12)
            check(f"rejects {why}", False, f"{bad!r} was accepted")
        except PageSpecError:
            check(f"rejects {why}", True)

    check("compact 1-3,7,10-12",
          _compact_pages([1, 2, 3, 7, 10, 11, 12]) == "1-3, 7, 10-12",
          _compact_pages([1, 2, 3, 7, 10, 11, 12]))
    check("compact empty", _compact_pages([]) == "none")

    pdf = ROOT / "inbox" / "sample-edition.pdf"
    if not pdf.exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        from make_sample_pdf import build

        build(pdf)

    total = pdf_page_count(pdf)
    check("sample pdf has 6 pages", total == 6, str(total))
    check("no selectors means every page", resolve_selection(pdf) is None)
    check("--pages", resolve_selection(pdf, pages="1-3") == {1, 2, 3})
    check("--skip-pages", resolve_selection(pdf, skip_pages="4-6") == {1, 2, 3})
    check("both combined",
          resolve_selection(pdf, pages="1-5", skip_pages="5") == {1, 2, 3, 4})
    try:
        resolve_selection(pdf, pages="2", skip_pages="2")
        check("rejects a selection with nothing left", False)
    except PageSpecError:
        check("rejects a selection with nothing left", True)

    selected = extract_pdf(pdf, pages_wanted={1, 3})
    check("only selected pages are read",
          [p.page_number for p in selected.pages] == [1, 3],
          str([p.page_number for p in selected.pages]))
    check("unselected pages are reported",
          selected.skipped_by_selection == [2, 4, 5, 6],
          str(selected.skipped_by_selection))
    check("page_count stays the true total", selected.page_count == 6,
          str(selected.page_count))

    print("\n[8] telling articles from adverts and listings")
    verdicts = {v.page_number: v for v in classify_document(extract_pdf(pdf))}
    expected = {
        1: "articles", 2: "articles", 3: "articles",
        4: "notices", 5: "tabular", 6: "sparse",
    }
    for page_no, want in expected.items():
        got = verdicts[page_no].kind
        check(f"page {page_no} classified {want}", got == want, f"got {got}")
    check("every verdict explains itself",
          all(len(v.reason) > 20 for v in verdicts.values()))
    check("article pages have real prose density",
          all(verdicts[n].sentence_count >= 10 for n in (1, 2, 3)))

    prep_sel = pipeline.prepare_edition(pdf, pages="1-3")
    check("prepare honours --pages",
          prep_sel.pages_seen == 3 and prep_sel.skipped_by_selection == [4, 5, 6],
          f"{prep_sel.pages_seen} pages, skipped {prep_sel.skipped_by_selection}")
    prompt_text = prep_sel.prompt_files[0].read_text(encoding="utf-8")
    check("no unselected page text reaches the prompt",
          "MATRIMONIAL" not in prompt_text and "SENSEX" not in prompt_text)
    check("selected page text does reach the prompt", "=== PAGE 3" in prompt_text)

    prep_junk = pipeline.prepare_edition(pdf, skip_junk=True)
    check("prepare --skip-junk drops the junk pages",
          prep_junk.skipped_as_junk == [4, 5, 6], str(prep_junk.skipped_as_junk))
    check("--skip-junk keeps the article pages", prep_junk.pages_seen == 3,
          str(prep_junk.pages_seen))
    check("--skip-junk records a reason per page",
          len(prep_junk.junk_reasons) == 3, str(prep_junk.junk_reasons))

    # Filters compose in order, and removing everything must not be reported
    # as a missing text layer -- that sends you chasing an OCR problem you
    # do not have.
    all_gone = pipeline.prepare_edition(pdf, pages="5", skip_junk=True)
    check("filters compose: a named junk page is still dropped",
          all_gone.pages_seen == 0, str(all_gone.pages_seen))
    check("filtering everything out is not reported as needing OCR",
          bool(all_gone.errors) and "OCR" not in all_gone.errors[0]
          and "dropped as junk" in all_gone.errors[0],
          str(all_gone.errors))

    print("\n[9] 'no articles on this page' replies")
    for sentinel in [
        "NO ARTICLES ON THIS PAGE",
        "There are no articles on this page - it is all classifieds.",
        "No news articles were found; the page is a market data table.",
    ]:
        parsed_none = parse_response(sentinel)
        check(f"accepted: {sentinel[:34]}",
              parsed_none.format_seen == "no-articles"
              and not parsed_none.articles
              and not parsed_none.warnings,
              f"{parsed_none.format_seen}, {parsed_none.warnings}")

    none_report = pipeline.load_response(
        "NO ARTICLES ON THIS PAGE", edition_id=prep_sel.edition_id, source="reply-x.txt"
    )
    check("loading a no-articles reply is not an error",
          not none_report.errors and none_report.no_articles_reported,
          str(none_report.errors))
    check("prompt spells out what to exclude",
          "WHAT TO LEAVE OUT ENTIRELY" in prompt_text
          and "classifieds" in prompt_text.lower()
          and "crossword" in prompt_text.lower())

    print("\n[10] html reading page")
    from newsagent.report import build_report

    markup, n = build_report(since=None)
    check("report includes every article", n == 8, str(n))
    check("report is a complete html document",
          markup.startswith("<!doctype html>") and markup.rstrip().endswith("</html>"))
    check("report embeds the article data", '"articles"' in markup)
    check("report carries the summaries", "dengue vaccine" in markup)
    check("report carries the full body text", "Mumbai brokerage" in markup)
    check("report carries topic chips", "Indian Economy" in markup)
    check("report is theme-aware", "prefers-color-scheme" in markup)
    check("report loads nothing from the network",
          "https://" not in markup
          and "http://" not in markup.replace("http://www.w3.org", ""))

    # A literal </script> inside the embedded JSON would end the script block
    # early and break the page, so "<" must be escaped in the JSON payload.
    _, _, tail = markup.partition("const DATA = ")
    json_line = tail.split("\n", 1)[0]
    check("embedded json cannot break out of its script tag",
          "<" not in json_line and "</" not in json_line)

    empty_markup, zero = build_report(since="1d")
    check("empty window still renders a valid page",
          zero == 0 and empty_markup.startswith("<!doctype html>"), str(zero))

    _, tagged = build_report(since=None, topics_only=True)
    check("topics_only keeps the tagged articles", tagged == 8, str(tagged))

    print(f"\n{'=' * 60}")
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print(f"  FAILED: {name}")
    print(f"{'=' * 60}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
