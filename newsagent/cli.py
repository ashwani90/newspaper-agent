"""Command line interface.

Getting a paper in, no API key needed:
    python -m newsagent pages PDF                 see what is on each page
    python -m newsagent prompt [PDF ...]          build prompts to paste
    python -m newsagent load <file|folder|->      load the chat reply back

Reading it, no API key needed:
    python -m newsagent html [--since 7d] [--topics-only]   <- browser
    python -m newsagent digest [--since 7d] [--full] [--all]
    python -m newsagent search "repo rate" [--topic ...] [--since 7d]
    python -m newsagent article 42
    python -m newsagent topics
    python -m newsagent editions
    python -m newsagent stats

Needs an API key:
    python -m newsagent ingest [PDF ...] [--force] [--max-pages N]
    python -m newsagent resummarise [--limit N]
    python -m newsagent ask "what's new in AI this week?"
    python -m newsagent chat
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from langchain_core.messages import HumanMessage
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import queries
from .agent import build_agent, last_text
from .config import CONFIG
from .llm import MissingApiKey
from .extract import (
    PageSpecError,
    classify_document,
    extract_pdf,
    pdf_page_count,
    resolve_selection,
)
from .report import write_report
from .pipeline import (
    _compact_pages,
    find_response_files,
    ingest_inbox,
    ingest_pdf,
    load_response,
    load_response_files,
    prepare_edition,
    summarise_one,
)
from .topics import parse_topics_file

# Newspaper text is full of curly quotes, en dashes and accented names, and
# the default Windows console codepage cannot encode them. Without this,
# printing an article raises UnicodeEncodeError or prints replacement
# characters.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable
        pass

console = Console()


def _progress(message: str) -> None:
    console.print(f"  [dim]{message}[/dim]")


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #


def cmd_ingest(args: argparse.Namespace) -> int:
    targets = [Path(p) for p in args.pdfs]
    if not targets:
        console.print(f"[bold]Ingesting every PDF in[/bold] {CONFIG.inbox}")
        reports = ingest_inbox(force=args.force, progress=_progress)
    else:
        reports = [
            ingest_pdf(
                p,
                force=args.force,
                max_pages=args.max_pages,
                pages=args.pages,
                skip_pages=args.skip_pages,
                skip_junk=args.skip_junk,
                progress=_progress,
            )
            for p in targets
        ]

    if not reports:
        console.print(
            f"[yellow]No PDFs found.[/yellow] Drop a newspaper PDF into "
            f"{CONFIG.inbox} and run this again."
        )
        return 1

    console.print()
    failed = False
    for report in reports:
        body = "\n".join(report.as_lines())
        style = "red" if report.errors else "green"
        if report.errors:
            failed = True
        console.print(Panel(body, border_style=style, expand=False))
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# manual workflow: prompt / load
# --------------------------------------------------------------------------- #


def cmd_prompt(args: argparse.Namespace) -> int:
    targets = [Path(p) for p in args.pdfs] or sorted(CONFIG.inbox.glob("*.pdf"))
    if not targets:
        console.print(
            f"[yellow]No PDFs found.[/yellow] Drop a newspaper PDF into "
            f"{CONFIG.inbox} and run this again."
        )
        return 1

    failed = False
    for pdf in targets:
        report = prepare_edition(
            pdf,
            max_pages=args.max_pages,
            max_chars=args.max_chars,
            pages_per_chunk=args.pages_per_chunk,
            include_layout=args.include_layout,
            pages=args.pages,
            skip_pages=args.skip_pages,
            skip_junk=args.skip_junk,
            progress=_progress,
        )
        console.print()
        console.print(
            Panel(
                "\n".join(report.as_lines()),
                border_style="red" if report.errors else "green",
                expand=False,
            )
        )
        if report.errors:
            failed = True
            continue

        console.print(
            f"\n[bold]Next:[/bold] paste each file into your LLM chat, save "
            f"each reply into the same folder, then run:\n"
            f"  [cyan]python -m newsagent load \"{report.prompt_dir}\" "
            f"--edition {report.edition_id}[/cyan]\n"
            f"[dim]Full instructions are in {report.prompt_dir}\\README.txt[/dim]"
        )
    return 1 if failed else 0


def cmd_pages(args: argparse.Namespace) -> int:
    """Show what is on each page, so you can pick which ones to process."""
    pdf = Path(args.pdf)
    if not pdf.exists():
        console.print(f"[red]Not found:[/red] {pdf}")
        return 1

    wanted = resolve_selection(pdf, pages=args.pages, skip_pages=args.skip_pages)
    doc = extract_pdf(pdf, pages_wanted=wanted)
    verdicts = classify_document(doc)

    table = Table(title=f"{pdf.name} - {pdf_page_count(pdf)} pages")
    table.add_column("Pg", justify="right")
    table.add_column("Looks like")
    table.add_column("Words", justify="right")
    table.add_column("First line", overflow="ellipsis", max_width=42)
    table.add_column("Why", overflow="fold")

    styles = {
        "articles": "green",
        "notices": "yellow",
        "tabular": "yellow",
        "sparse": "yellow",
        "listings": "yellow",
        "no-text": "red",
    }
    for verdict in verdicts:
        style = styles.get(verdict.kind, "")
        table.add_row(
            str(verdict.page_number),
            f"[{style}]{verdict.kind}[/{style}]" if style else verdict.kind,
            str(verdict.word_count),
            verdict.preview or "[dim]-[/dim]",
            verdict.reason,
        )
    console.print(table)

    article_pages = [v.page_number for v in verdicts if v.looks_like_articles]
    other = [v.page_number for v in verdicts if not v.looks_like_articles]

    if article_pages:
        console.print(
            f"\n[bold]Article pages:[/bold] "
            f"[green]{_compact_pages(article_pages)}[/green]"
        )
    if other:
        console.print(
            f"[bold]Everything else:[/bold] [yellow]{_compact_pages(other)}[/yellow]"
        )
    if article_pages and other:
        console.print(
            f"\nProcess only the article pages with:\n"
            f"  [cyan]python -m newsagent prompt \"{pdf}\" "
            f"--pages {_compact_pages(article_pages).replace(' ', '')}[/cyan]\n"
            f"or let it decide for you:\n"
            f"  [cyan]python -m newsagent prompt \"{pdf}\" --skip-junk[/cyan]"
        )
    console.print(
        "\n[dim]These are guesses from text statistics, not certainties. "
        "Check a page with 'newsagent pages' before trusting --skip-junk on "
        "an unfamiliar paper.[/dim]"
    )
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    if args.target == "-":
        raw = sys.stdin.read()
        reports = [
            load_response(
                raw, edition_id=args.edition, source="stdin", progress=_progress
            )
        ]
    else:
        target = Path(args.target)
        if not target.exists():
            console.print(f"[red]Not found:[/red] {target}")
            return 1
        if target.is_dir():
            files = find_response_files(target)
            if not files:
                console.print(
                    f"[yellow]No reply files in {target}.[/yellow] Save each "
                    f"chat reply there as reply-01.txt, reply-02.txt, ... "
                    f"(chunk-*.txt files are the prompts and are ignored)."
                )
                return 1
            console.print(f"Loading {len(files)} reply file(s) from {target}")
            reports = load_response_files(
                files, edition_id=args.edition, progress=_progress
            )
        else:
            reports = load_response_files(
                [target], edition_id=args.edition, progress=_progress
            )

    console.print()
    failed = False
    for report in reports:
        if report.errors:
            failed = True
        console.print(
            Panel(
                "\n".join(report.as_lines()),
                border_style="red" if report.errors else "green",
                expand=False,
            )
        )

    if not failed:
        console.print(
            "\n[bold]Next:[/bold] read it in your browser with "
            "[cyan]python -m newsagent html --since all[/cyan]\n"
            "[dim]or in the terminal with 'newsagent digest --since all --full'"
            "[/dim]"
        )
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# digest
# --------------------------------------------------------------------------- #


def cmd_digest(args: argparse.Namespace) -> int:
    window = None if args.since.lower() in {"all", "any"} else args.since
    data = queries.digest(since=window, topics_only=not args.all)

    console.print()
    console.rule(f"[bold]Your digest[/bold]  ({data['window']})")
    console.print(
        f"[dim]{data['matched_your_topics']} of {data['articles_considered']} "
        f"summarised articles match your topics[/dim]\n"
    )

    if not data["by_topic"]:
        console.print(
            "[yellow]Nothing matched your topics yet.[/yellow] Either no papers "
            "are ingested, or nothing in them touched your interests. Try "
            "[bold]--all[/bold] to see everything."
        )

    for topic, articles in data["by_topic"].items():
        console.print(f"[bold cyan]{topic}[/bold cyan]  [dim]({len(articles)})[/dim]")
        for art in articles:
            _print_article_line(art, topic, full=args.full)
        console.print()

    if args.all and data.get("untagged"):
        console.print(
            f"[bold]Not in your topics[/bold] [dim]({data['untagged_count']})[/dim]"
        )
        for art in data["untagged"]:
            _print_article_line(art, None, full=args.full)
    return 0


def _print_article_line(art: dict, topic: str | None, full: bool = False) -> None:
    summary = art.get("summary") or {}
    one_liner = summary.get("one_liner", "(no summary yet)")
    conf = ""
    if topic:
        for tag in art.get("topics", []):
            if tag["topic"] == topic:
                conf = f" [dim]{tag['confidence']:.0%}[/dim]"
                break

    if full:
        # Reading view: headline first, then the summary and every bullet.
        console.print(f"  [bold]{art['headline']}[/bold]{conf}")
        console.print(f"  {one_liner}")
        for bullet in summary.get("bullets", []):
            console.print(f"    - {bullet}")
        if summary.get("why_it_matters"):
            console.print(
                f"    [italic dim]Why: {summary['why_it_matters']}[/italic dim]"
            )
        meta = [f"p{art['page']}, #{art['article_id']}"]
        if art.get("byline"):
            meta.append(art["byline"])
        if summary.get("read_minutes"):
            meta.append(f"{summary['read_minutes']} min read")
        console.print(f"    [dim]{'  |  '.join(meta)}[/dim]")
        console.print()
        return

    # Scan view: one line of substance, then where to find it.
    console.print(f"  * {one_liner}{conf}")
    console.print(
        f"    [dim]{art['headline']}  -  p{art['page']}, #{art['article_id']}"
        f"{', ' + art['source'] if art.get('source') else ''}[/dim]"
    )


# --------------------------------------------------------------------------- #
# html reading page
# --------------------------------------------------------------------------- #


def cmd_html(args: argparse.Namespace) -> int:
    window = None if args.since.lower() in {"all", "any"} else args.since
    out = Path(args.out) if args.out else None
    target, count = write_report(
        out, since=window, topics_only=args.topics_only
    )

    if not count:
        console.print(
            f"[yellow]No summarised articles in that window.[/yellow] Wrote an "
            f"empty page to {target}. Try [bold]--since all[/bold], or load a "
            f"paper first."
        )
    else:
        size_kb = target.stat().st_size / 1024
        console.print(
            f"[green]Wrote[/green] {target}  [dim]({count} articles, "
            f"{size_kb:.0f} KB)[/dim]"
        )

    if not args.no_open:
        webbrowser.open(target.resolve().as_uri())
        console.print("[dim]Opening in your browser...[/dim]")
    else:
        console.print(f"[dim]Open it with: start {target}[/dim]")
    return 0


# --------------------------------------------------------------------------- #
# search / topics / article / editions / stats
# --------------------------------------------------------------------------- #


def cmd_search(args: argparse.Namespace) -> int:
    results = queries.search_articles(
        args.query,
        limit=args.limit,
        topic=args.topic,
        since=args.since,
    )
    if not results:
        console.print(f"[yellow]Nothing found for[/yellow] {args.query!r}")
        return 1
    console.print()
    console.rule(f"[bold]{len(results)} result(s) for {args.query!r}[/bold]")
    for art in results:
        summary = art.get("summary") or {}
        console.print(f"\n[bold]{art['headline']}[/bold]")
        meta = [f"p{art['page']}", f"#{art['article_id']}"]
        if art.get("source"):
            meta.append(art["source"])
        if art.get("edition_date"):
            meta.append(art["edition_date"])
        console.print(f"[dim]{'  |  '.join(meta)}[/dim]")
        if summary.get("one_liner"):
            console.print(f"  {summary['one_liner']}")
        for bullet in summary.get("bullets", []):
            console.print(f"    - {bullet}")
        if art["topics"]:
            tags = ", ".join(
                f"{t['topic']} {t['confidence']:.0%}" for t in art["topics"]
            )
            console.print(f"  [cyan]topics:[/cyan] {tags}")
    return 0


def cmd_topics(_args: argparse.Namespace) -> int:
    rows = queries.list_topics()
    specs = parse_topics_file()
    table = Table(title=f"Topics from {CONFIG.topics_file}")
    table.add_column("Topic", style="bold cyan")
    table.add_column("Articles", justify="right")
    table.add_column("Keywords", overflow="fold")
    table.add_column("Active", justify="center")
    for row in rows:
        table.add_row(
            row["topic"],
            str(row["article_count"]),
            ", ".join(row["keywords"]) or "[dim]-[/dim]",
            "yes" if row["active"] else "[dim]no[/dim]",
        )
    console.print(table)
    if not specs:
        console.print(
            f"[yellow]topics.txt is empty or missing.[/yellow] "
            f"Add one topic per line to {CONFIG.topics_file}"
        )
    else:
        console.print(
            f"[dim]Edit {CONFIG.topics_file} to change these. "
            f"Changes apply on the next run - no re-ingest needed for new "
            f"tagging, but run 'resummarise --all' to re-tag old articles."
            f"[/dim]"
        )
    return 0


def cmd_article(args: argparse.Namespace) -> int:
    art = queries.get_article(args.article_id, include_body=True)
    if art is None:
        console.print(f"[red]No article with id {args.article_id}[/red]")
        return 1
    summary = art.get("summary") or {}
    console.print()
    console.rule(f"[bold]{art['headline']}[/bold]")
    meta = [f"page {art['page']}", f"#{art['article_id']}"]
    if art.get("byline"):
        meta.append(art["byline"])
    if art.get("source"):
        meta.append(art["source"])
    if art.get("edition_date"):
        meta.append(art["edition_date"])
    console.print(f"[dim]{'  |  '.join(meta)}[/dim]\n")

    if summary:
        console.print("[bold]Summary[/bold]")
        console.print(f"  {summary.get('one_liner', '')}")
        for bullet in summary.get("bullets", []):
            console.print(f"  - {bullet}")
        if summary.get("why_it_matters"):
            console.print(f"\n  [italic]Why it matters:[/italic] {summary['why_it_matters']}")
        if summary.get("entities"):
            console.print(f"  [dim]entities: {', '.join(summary['entities'])}[/dim]")
    if art["topics"]:
        tags = ", ".join(
            f"{t['topic']} ({t['confidence']:.0%}, {t['matched_by']})"
            for t in art["topics"]
        )
        console.print(f"\n[cyan]Your topics:[/cyan] {tags}")
        for tag in art["topics"]:
            if tag.get("rationale"):
                console.print(f"  [dim]{tag['topic']}: {tag['rationale']}[/dim]")

    console.print("\n[bold]Full text[/bold]")
    console.print(art.get("body_text", ""))
    return 0


def cmd_editions(_args: argparse.Namespace) -> int:
    rows = queries.list_editions()
    if not rows:
        console.print("[yellow]No editions ingested yet.[/yellow]")
        return 1
    table = Table(title="Ingested editions")
    table.add_column("#", justify="right")
    table.add_column("Source")
    table.add_column("Date")
    table.add_column("Pages", justify="right")
    table.add_column("Articles", justify="right")
    table.add_column("Status")
    for row in rows:
        table.add_row(
            str(row["edition_id"]),
            row["source"] or "-",
            row["edition_date"] or "-",
            str(row["pages"]),
            str(row["articles"]),
            row["status"],
        )
    console.print(table)
    for row in rows:
        if row.get("note"):
            console.print(f"[yellow]#{row['edition_id']}: {row['note']}[/yellow]")
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    data = queries.stats()
    table = Table(title="Library", show_header=False)
    table.add_column("", style="bold")
    table.add_column("", justify="right")
    for key, value in data.items():
        table.add_row(key.replace("_", " "), str(value))
    console.print(table)
    console.print(f"[dim]database: {CONFIG.db_path}[/dim]")
    return 0


def cmd_resummarise(args: argparse.Namespace) -> int:
    """Fill in missing summaries, or re-tag everything after editing topics.txt."""
    specs = parse_topics_file()
    if args.all:
        with_ids = [a["article_id"] for a in _all_article_ids()]
    else:
        with_ids = queries.unsummarised_article_ids(limit=args.limit)

    if not with_ids:
        console.print("[green]Nothing to do - every article has a summary.[/green]")
        return 0

    console.print(f"Summarising {len(with_ids)} article(s)")
    failures = 0
    for index, article_id in enumerate(with_ids, start=1):
        try:
            summarise_one(
                article_id, specs, progress=_progress, index=index, total=len(with_ids)
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            console.print(f"  [red]article {article_id}: {exc}[/red]")
    console.print(
        f"[green]done[/green] - {len(with_ids) - failures} succeeded, {failures} failed"
    )
    return 1 if failures else 0


def _all_article_ids() -> list[dict]:
    from sqlalchemy import select

    from .db import Article, session_scope

    with session_scope() as session:
        return [
            {"article_id": aid}
            for aid in session.scalars(select(Article.id).order_by(Article.id)).all()
        ]


# --------------------------------------------------------------------------- #
# agent
# --------------------------------------------------------------------------- #


def cmd_ask(args: argparse.Namespace) -> int:
    agent = build_agent(with_memory=False)
    question = " ".join(args.question)
    with console.status("[dim]thinking...[/dim]"):
        result = agent.invoke(
            {"messages": [HumanMessage(content=question)]},
            config={"recursion_limit": 40},
        )
    console.print()
    console.print(Markdown(last_text(result)))
    return 0


def cmd_chat(_args: argparse.Namespace) -> int:
    agent = build_agent(with_memory=True)
    config = {"configurable": {"thread_id": "chat"}, "recursion_limit": 40}
    console.print(
        Panel(
            "Ask about your papers. Try:\n"
            "  what should I read today?\n"
            "  anything on the RBI this week?\n"
            "  what are my topics?\n\n"
            "[dim]Ctrl-C or 'exit' to quit.[/dim]",
            title="newspaper agent",
            border_style="cyan",
            expand=False,
        )
    )
    while True:
        try:
            question = console.input("\n[bold cyan]you >[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return 0
        if question.lower() in {"exit", "quit", ":q"}:
            console.print("[dim]bye[/dim]")
            return 0
        if not question:
            continue
        try:
            with console.status("[dim]thinking...[/dim]"):
                result = agent.invoke({"messages": [HumanMessage(content=question)]}, config)
        except KeyboardInterrupt:
            console.print("[dim]cancelled[/dim]")
            continue
        console.print()
        console.print(Markdown(last_text(result)))


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def _add_page_selection(parser: argparse.ArgumentParser) -> None:
    """The --pages / --skip-pages / --skip-junk trio, shared by two commands."""
    parser.add_argument(
        "--pages",
        default=None,
        metavar="SPEC",
        help=(
            "only these pages, e.g. '1-4,7,10-12'. Also accepts '-6', '8-', "
            "'odd', 'even'. Unselected pages are never read"
        ),
    )
    parser.add_argument(
        "--skip-pages",
        default=None,
        metavar="SPEC",
        help="same syntax, but these pages are left out",
    )
    parser.add_argument(
        "--skip-junk",
        action="store_true",
        help=(
            "also drop pages that look like adverts, classifieds or data "
            "tables (see 'newsagent pages' first)"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newsagent",
        description="Read newspaper PDFs so you do not have to.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "pages",
        help="[no API key] show what is on each page, to pick which to process",
    )
    p.add_argument("pdf")
    p.add_argument("--pages", default=None, metavar="SPEC", help="only inspect these")
    p.add_argument("--skip-pages", default=None, metavar="SPEC")
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser(
        "prompt",
        help="[no API key] build chat prompts from a PDF to paste into an LLM",
    )
    p.add_argument("pdfs", nargs="*", help="PDF paths (default: everything in inbox/)")
    p.add_argument(
        "--max-pages", type=int, default=None, help="only read the first N pages"
    )
    p.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="page-text budget per prompt file (default 60000)",
    )
    p.add_argument(
        "--pages-per-chunk",
        type=int,
        default=None,
        help="fixed pages per prompt file, instead of the character budget",
    )
    _add_page_selection(p)
    p.add_argument(
        "--include-layout",
        action="store_true",
        help=(
            "also include the layout-preserving page rendering. About 5x "
            "larger; use it if articles come back split badly"
        ),
    )
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser(
        "load",
        help="[no API key] load a chat reply (file, folder, or - for stdin)",
    )
    p.add_argument(
        "target",
        help="reply file, a folder of replies, or - to read from stdin",
    )
    p.add_argument(
        "--edition",
        type=int,
        default=None,
        help="edition id these replies belong to (default: most recent)",
    )
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("ingest", help="parse, summarise and store newspaper PDFs")
    p.add_argument("pdfs", nargs="*", help="PDF paths (default: everything in inbox/)")
    p.add_argument("--force", action="store_true", help="re-process an ingested edition")
    p.add_argument(
        "--max-pages", type=int, default=None, help="only read the first N pages"
    )
    _add_page_selection(p)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("digest", help="today's reading list, grouped by your topics")
    p.add_argument("--since", default="7d", help="window: 1d, 7d, 2w, ISO date, or all")
    p.add_argument("--all", action="store_true", help="include articles outside your topics")
    p.add_argument(
        "--full",
        action="store_true",
        help="reading view: show every bullet and why-it-matters, not just the one-liner",
    )
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser(
        "html", help="build an HTML reading page and open it in your browser"
    )
    p.add_argument("--since", default="7d", help="window: 1d, 7d, 2w, ISO date, or all")
    p.add_argument(
        "--topics-only",
        action="store_true",
        help="include only articles matching your topics",
    )
    p.add_argument("-o", "--out", default=None, help="output path for the HTML file")
    p.add_argument(
        "--no-open", action="store_true", help="just write the file, do not open it"
    )
    p.set_defaults(func=cmd_html)

    p = sub.add_parser("search", help="full-text search the stored articles")
    p.add_argument("query")
    p.add_argument("--topic", default=None)
    p.add_argument("--since", default=None)
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("topics", help="show your topics and their article counts")
    p.set_defaults(func=cmd_topics)

    p = sub.add_parser("article", help="show one article in full")
    p.add_argument("article_id", type=int)
    p.set_defaults(func=cmd_article)

    p = sub.add_parser("editions", help="list ingested newspaper editions")
    p.set_defaults(func=cmd_editions)

    p = sub.add_parser("stats", help="library counts")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser(
        "resummarise", help="summarise articles that have no summary yet"
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="redo every article (use after editing topics.txt to re-tag)",
    )
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=cmd_resummarise)

    p = sub.add_parser("ask", help="ask the agent one question")
    p.add_argument("question", nargs="+")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("chat", help="interactive conversation with the agent")
    p.set_defaults(func=cmd_chat)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PageSpecError as exc:
        console.print(f"[red]Bad page selection:[/red] {exc}")
        return 2
    except MissingApiKey as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted - re-run to resume where it stopped[/dim]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
