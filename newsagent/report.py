"""Build a self-contained HTML page for reading the summarised paper.

One file, no server, no network. Every article is embedded as JSON and the
filtering, searching and read-tracking happen in the browser, so the page
works offline, opens with a double-click, and can be copied anywhere.

Regenerate it after each `load` -- it is a snapshot, not a live view.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import queries
from .config import CONFIG
from webapp.config import WEB_CONFIG

# A closing script tag inside embedded JSON would end the <script> block
# early, so the three characters that could start markup are escaped. This is
# the standard JSON-in-HTML precaution, and it keeps the JSON valid.
_JSON_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}


def _embed_json(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for char, escape in _JSON_ESCAPES.items():
        text = text.replace(char, escape)
    return text


CSS = """\
:root {
  --bg: #f7f6f3;
  --surface: #ffffff;
  --border: #e2ded6;
  --ink: #1c1a17;
  --ink-soft: #5c574e;
  --ink-faint: #8a8378;
  --accent: #8c2f1e;
  --accent-soft: #f3e6e2;
  --chip: #efece5;
  --read: #a8a29a;
  --shadow: 0 1px 2px rgba(28, 26, 23, .06);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161513;
    --surface: #1e1d1a;
    --border: #322f2a;
    --ink: #eae7e0;
    --ink-soft: #a9a49a;
    --ink-faint: #78736a;
    --accent: #e8836b;
    --accent-soft: #2e211d;
    --chip: #272521;
    --read: #5d584f;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 820px; margin: 0 auto; padding: 0 20px 80px; }

header.masthead {
  text-align: center;
  padding: 34px 0 18px;
  border-bottom: 2px solid var(--ink);
  margin-bottom: 0;
}
.masthead h1 {
  margin: 0;
  font-family: Georgia, "Times New Roman", serif;
  font-size: clamp(28px, 6vw, 44px);
  font-weight: 700;
  letter-spacing: -.5px;
}
.masthead .dateline {
  margin-top: 8px;
  color: var(--ink-soft);
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: .1em;
}

.toolbar {
  position: sticky;
  top: 0;
  z-index: 5;
  background: var(--bg);
  padding: 14px 0 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 22px;
}
.toolbar .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
input[type="search"] {
  flex: 1 1 220px;
  min-width: 0;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface);
  color: var(--ink);
  font-size: 15px;
}
input[type="search"]:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.toggle {
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--ink-soft); font-size: 14px; white-space: nowrap; cursor: pointer;
}
button.linky {
  background: none; border: none; padding: 0; cursor: pointer;
  color: var(--accent); font-size: 14px; text-decoration: underline;
}
.chips { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 11px; }
.chip {
  padding: 5px 11px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--chip); color: var(--ink-soft);
  font-size: 13px; cursor: pointer; white-space: nowrap;
}
.chip[aria-pressed="true"] {
  background: var(--accent); border-color: var(--accent); color: #fff;
}
.chip .n { opacity: .65; margin-left: 4px; font-variant-numeric: tabular-nums; }

.count { color: var(--ink-faint); font-size: 13px; margin: 0 0 16px; }

.topic-head {
  font-size: 12px; text-transform: uppercase; letter-spacing: .12em;
  color: var(--accent); font-weight: 700;
  margin: 30px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border);
}
.topic-head:first-of-type { margin-top: 6px; }

article {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  margin-bottom: 12px;
  box-shadow: var(--shadow);
}
article.is-read { opacity: .55; }
article.is-read h2 { color: var(--read); }
h2 {
  margin: 0 0 7px;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 20px; line-height: 1.25; font-weight: 700;
}
.oneliner { margin: 0 0 10px; color: var(--ink); }
ul.bullets { margin: 0 0 10px; padding-left: 20px; color: var(--ink-soft); }
ul.bullets li { margin-bottom: 4px; }
.why {
  margin: 0 0 10px; padding: 8px 11px;
  background: var(--accent-soft); border-radius: 6px;
  font-size: 14px; color: var(--ink-soft);
}
.why b { color: var(--ink); }
.meta {
  display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  font-size: 12.5px; color: var(--ink-faint);
  border-top: 1px solid var(--border); padding-top: 9px;
}
.tag {
  background: var(--chip); border-radius: 4px; padding: 2px 7px;
  color: var(--ink-soft); font-size: 12px;
}
.meta .spacer { flex: 1; }
details.full { margin-top: 10px; }
details.full summary {
  cursor: pointer; color: var(--accent); font-size: 13.5px;
  list-style: none; display: inline-block;
}
details.full summary::-webkit-details-marker { display: none; }
details.full summary::before { content: "▸ "; }
details.full[open] summary::before { content: "▾ "; }
.bodytext {
  margin-top: 10px; padding: 13px 15px;
  background: var(--bg); border: 1px solid var(--border); border-radius: 7px;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 15.5px; line-height: 1.68;
  white-space: pre-wrap; max-height: 460px; overflow-y: auto;
}
.nobody { color: var(--ink-faint); font-size: 13.5px; font-style: italic; }
.empty {
  text-align: center; padding: 54px 20px; color: var(--ink-faint);
}
footer { margin-top: 40px; color: var(--ink-faint); font-size: 12.5px; text-align: center; }
footer code {
  background: var(--chip); padding: 2px 6px; border-radius: 4px; font-size: 12px;
}
@media print {
  .toolbar, details.full, footer { display: none; }
  article { break-inside: avoid; box-shadow: none; }
  article.is-read { opacity: 1; }
}
"""

JS = """\
const $ = (s) => document.querySelector(s);
const listEl = $("#list");
const countEl = $("#count");
const searchEl = $("#q");
const unreadEl = $("#unreadOnly");
const groupEl = $("#grouped");

const STORE = "newsagent.read." + DATA.db_key;
let readSet = new Set();
try { readSet = new Set(JSON.parse(localStorage.getItem(STORE) || "[]")); }
catch (e) { readSet = new Set(); }

function persist() {
  try { localStorage.setItem(STORE, JSON.stringify([...readSet])); }
  catch (e) { /* private window, or site data blocked -- fine, just no memory */ }
}

let activeTopic = null;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

function haystack(a) {
  if (a._hay) return a._hay;
  const s = a.summary || {};
  a._hay = [
    a.headline, a.byline, a.section, s.one_liner, s.why_it_matters, s.category,
    (s.bullets || []).join(" "), (s.entities || []).join(" "),
    (a.topics || []).map((t) => t.topic).join(" "), a.body_text
  ].join(" ").toLowerCase();
  return a._hay;
}

function matching() {
  const q = searchEl.value.trim().toLowerCase();
  const terms = q ? q.split(/\\s+/) : [];
  return DATA.articles.filter((a) => {
    if (activeTopic && !(a.topics || []).some((t) => t.topic === activeTopic)) return false;
    if (unreadEl.checked && readSet.has(a.article_id)) return false;
    if (terms.length) {
      const h = haystack(a);
      if (!terms.every((t) => h.includes(t))) return false;
    }
    return true;
  });
}

function confFor(a, topic) {
  const hit = (a.topics || []).find((t) => t.topic === topic);
  return hit ? Math.round(hit.confidence * 100) + "%" : "";
}

function cardHtml(a, topicContext) {
  const s = a.summary || {};
  const isRead = readSet.has(a.article_id);
  const bullets = (s.bullets || []).map((b) => `<li>${esc(b)}</li>`).join("");
  const tags = (a.topics || [])
    .map((t) => `<span class="tag">${esc(t.topic)} ${Math.round(t.confidence * 100)}%</span>`)
    .join("");
  const meta = [];
  if (a.page != null) meta.push("p" + a.page);
  meta.push("#" + a.article_id);
  if (a.byline) meta.push(esc(a.byline));
  if (s.read_minutes) meta.push(s.read_minutes + " min read");
  if (a.edition_date) meta.push(esc(a.edition_date));

  const body = a.body_text
    ? `<div class="bodytext">${esc(a.body_text)}</div>`
    : `<p class="nobody">The full text could not be located in the page for this
       article, so only the summary is stored.</p>`;

  const conf = topicContext ? confFor(a, topicContext) : "";

  return `<article class="${isRead ? "is-read" : ""}" data-id="${a.article_id}">
    <h2>${esc(a.headline)}</h2>
    <p class="oneliner">${esc(s.one_liner || "")}</p>
    ${bullets ? `<ul class="bullets">${bullets}</ul>` : ""}
    ${s.why_it_matters ? `<p class="why"><b>Why it matters:</b> ${esc(s.why_it_matters)}</p>` : ""}
    <div class="meta">
      ${tags}<span class="spacer"></span>
      <span>${meta.join(" &middot; ")}</span>
      <button class="linky mark">${isRead ? "mark unread" : "mark read"}</button>
    </div>
    <details class="full"><summary>Full article text</summary>${body}</details>
  </article>`;
}

function render() {
  const rows = matching();
  const total = DATA.articles.length;
  const unread = DATA.articles.filter((a) => !readSet.has(a.article_id)).length;
  countEl.textContent =
    `${rows.length} of ${total} article${total === 1 ? "" : "s"} shown` +
    ` \\u00b7 ${unread} unread`;

  if (!rows.length) {
    listEl.innerHTML = `<p class="empty">Nothing matches. Clear the search or
      pick a different topic.</p>`;
    return;
  }

  if (!groupEl.checked || activeTopic) {
    listEl.innerHTML = rows.map((a) => cardHtml(a, activeTopic)).join("");
    return;
  }

  // Grouped view: an article appears under each topic it matched, then any
  // untagged ones last.
  const groups = new Map();
  const untagged = [];
  for (const a of rows) {
    if (!(a.topics || []).length) { untagged.push(a); continue; }
    for (const t of a.topics) {
      if (!groups.has(t.topic)) groups.set(t.topic, []);
      groups.get(t.topic).push(a);
    }
  }
  const ordered = [...groups.entries()].sort((x, y) => y[1].length - x[1].length);
  let out = "";
  for (const [topic, items] of ordered) {
    items.sort((x, y) => (confFor(y, topic) > confFor(x, topic) ? 1 : -1));
    out += `<h3 class="topic-head">${esc(topic)} &middot; ${items.length}</h3>`;
    out += items.map((a) => cardHtml(a, topic)).join("");
  }
  if (untagged.length) {
    out += `<h3 class="topic-head">Not in your topics &middot; ${untagged.length}</h3>`;
    out += untagged.map((a) => cardHtml(a, null)).join("");
  }
  listEl.innerHTML = out;
}

// One listener for every mark-read button, so re-rendering cannot leak them.
listEl.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button.mark");
  if (!btn) return;
  const id = Number(btn.closest("article").dataset.id);
  if (readSet.has(id)) readSet.delete(id); else readSet.add(id);
  persist();
  render();
});

$("#chips").addEventListener("click", (ev) => {
  const chip = ev.target.closest(".chip");
  if (!chip) return;
  const topic = chip.dataset.topic || null;
  activeTopic = activeTopic === topic ? null : topic;
  for (const c of document.querySelectorAll("#chips .chip")) {
    c.setAttribute("aria-pressed", String((c.dataset.topic || null) === activeTopic));
  }
  render();
});

searchEl.addEventListener("input", render);
unreadEl.addEventListener("change", render);
groupEl.addEventListener("change", render);
$("#allRead").addEventListener("click", () => {
  for (const a of matching()) readSet.add(a.article_id);
  persist(); render();
});
$("#noneRead").addEventListener("click", () => {
  readSet.clear(); persist(); render();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "/" && document.activeElement !== searchEl) {
    ev.preventDefault(); searchEl.focus();
  }
  if (ev.key === "Escape" && document.activeElement === searchEl) {
    searchEl.value = ""; searchEl.blur(); render();
  }
});

render();
"""


def build_report(
    *,
    since: str | None = None,
    topics_only: bool = False,
    source: str | None = None,
) -> tuple[str, int]:
    """Render the HTML page. Returns (html, article count)."""
    articles = queries.export_articles(
        since=since, topics_only=topics_only, source=source, include_body=True
    )

    # Topic chip counts, most-used first.
    counts: dict[str, int] = {}
    for article in articles:
        for tag in article["topics"]:
            counts[tag["topic"]] = counts.get(tag["topic"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    sources = [a["source"] for a in articles if a.get("source")]
    dates = sorted({a["edition_date"] for a in articles if a.get("edition_date")})
    title = sources[0] if sources else "Your newspaper"
    if len(set(sources)) > 1:
        title = "Your newspapers"

    if not dates:
        dateline = "no edition date"
    elif len(dates) == 1:
        dateline = dates[0]
    else:
        dateline = f"{dates[0]} to {dates[-1]}"
    window = since or "all editions"

    chips = ['<button class="chip" aria-pressed="false">All topics'
             f'<span class="n">{len(articles)}</span></button>']
    for topic, count in ordered:
        chips.append(
            f'<button class="chip" aria-pressed="false" data-topic="{html.escape(topic, quote=True)}">'
            f'{html.escape(topic)}<span class="n">{count}</span></button>'
        )

    payload = {
        # Read/unread state's localStorage namespace. There is one shared
        # Postgres database now (no per-file SQLite library to key by).
        "db_key": "newsagent",
        "articles": articles,
    }

    generated = datetime.now().strftime("%d %b %Y, %H:%M")

    return (
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} - {html.escape(dateline)}</title>
<style>
{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="masthead">
  <h1>{html.escape(title)}</h1>
  <div class="dateline">{html.escape(dateline)} &middot; {len(articles)} summarised articles</div>
</header>

<div class="toolbar">
  <div class="row">
    <input type="search" id="q" placeholder="Search headlines, summaries, full text&hellip;  (press /)" autocomplete="off">
    <label class="toggle"><input type="checkbox" id="grouped" checked> group by topic</label>
    <label class="toggle"><input type="checkbox" id="unreadOnly"> unread only</label>
  </div>
  <div class="chips" id="chips">{"".join(chips)}</div>
  <div class="row" style="margin-top:10px">
    <button class="linky" id="allRead">mark all shown as read</button>
    <button class="linky" id="noneRead">reset all</button>
  </div>
</div>

<p class="count" id="count"></p>
<div id="list"></div>

<footer>
  Generated {html.escape(generated)} from PostgreSQL ({html.escape(WEB_CONFIG.postgres_db)})
  &middot; window: {html.escape(str(window))}<br>
  Snapshot, not live &mdash; run <code>newsagent html</code> again after loading a new paper.
</footer>

</div>
<script>
const DATA = {_embed_json(payload)};
</script>
<script>
{JS}</script>
</body>
</html>
""",
        len(articles),
    )


def write_report(
    out_path: Path | None = None,
    *,
    since: str | None = None,
    topics_only: bool = False,
    source: str | None = None,
) -> tuple[Path, int]:
    """Render the page and write it to disk. Returns (path, article count)."""
    target = out_path or (CONFIG.reports_dir / "digest.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    markup, count = build_report(
        since=since, topics_only=topics_only, source=source
    )
    target.write_text(markup, encoding="utf-8")
    return target, count
