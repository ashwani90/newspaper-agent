# newspaper-agent

Reads a newspaper PDF so you don't have to.

Point it at an e-paper PDF and it parses the pages, recovers the individual
articles out of the multi-column layout, summarises each one, stores
everything in SQLite, and tags every article against the topics you keep in
[`topics.txt`](topics.txt).

**Quick Start:** 
- **From Claude Code:** `/newspaper today.pdf` — uses the registered skill
- **Command line:** See [Command Reference](#command-reference-all-commands) below for complete usage
- **Skill docs:** See [skills/README.md](skills/README.md)

## Using from Claude Code (Recommended)

A skill is registered for use in Claude Code. Simply invoke it:

```
/newspaper today.pdf
/newspaper today.pdf pages 1-8,12
/newspaper today.pdf --skip-junk
```

The skill walks you through the entire manual workflow:
1. Classify pages (if needed)
2. Build summarization prompts
3. Guide you through summarizing each chunk
4. Load results into database
5. Open interactive reading page

**No API key required.** Cost: free (you provide the summaries).

See [skills/README.md](skills/README.md) for full skill documentation.

---

## Running from Command Line

There are **two ways to run it**:

| | Manual (default) | Automatic |
|---|---|---|
| Summarising | you paste a prompt into your LLM chat window | API call |
| API key | not needed | required |
| Cost | free (uses your existing chat subscription) | ~$2–4 per edition |
| Effort | 1–2 copy-pastes per paper | one command |

Everything after summarising — the database, search, digests, topics — is
identical either way.

```
> python -m newsagent digest

Indian Economy
  * The RBI held the repo rate at 6.25% after retail inflation fell to a
    nine-month low of 4.1% in February.                        (p1, #1)
Artificial Intelligence
  * Anthropic will open its first India office in Bengaluru this year,
    hiring about 300 staff over eighteen months.               (p1, #2)
```

---

## Setup

```bash
cd E:\newspaper-agent
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

That's it for the manual workflow — no API key, no `.env` needed.

### Try it right now

A sample newspaper PDF and a sample chat reply ship with the project, so you
can see the whole thing work before touching a real paper:

```bash
.venv\Scripts\python.exe -m newsagent prompt inbox\sample-edition.pdf
.venv\Scripts\python.exe -m newsagent load prompts\edition-001 --edition 1
.venv\Scripts\python.exe -m newsagent html --since all
```

The first command builds the prompt; the second loads a reply that is already
sitting in that folder (`reply-01.txt`) as if you had pasted it into a chat
yourself. Open `prompts\edition-001\chunk-01.txt` to see exactly what gets
pasted, and `reply-01.txt` to see the shape of the answer it expects.

---

## The manual workflow

### 0. See what's on each page

A newspaper is mostly not articles. Before processing anything:

```bash
.venv\Scripts\python.exe -m newsagent pages inbox\today.pdf
```

```
 Pg  Looks like  Words  First line              Why
  1  articles      371  THE MORNING CHRONICLE   371 words at 4.0 sentences per 100 words
  2  articles      365  Fintech startup Kaviya  365 words at 3.8 sentences per 100 words
  3  articles      313  Monsoon forecast        313 words at 4.5 sentences per 100 words
  4  notices       315  Alliance invited, 28/5  12 classified headings, 1.0 sentences/100 words
  5  tabular       534  MARKET WATCH            64% digits and 12 table headings
  6  sparse         14  UPTO 60% OFF            only 14 words on the whole page

Article pages: 1-3
Everything else: 4-6
```

It then prints the exact command to process just the article pages. The
classifier keys on **prose density** — sentences per 100 words. Reporting
lands around 3–5; classifieds, listings and tables sit under 1, because they
are fragments rather than sentences.

These are guesses from text statistics, not certainties. Run `pages` on an
unfamiliar paper before trusting `--skip-junk` on it.

### 1. Build the prompts

```bash
.venv\Scripts\python.exe -m newsagent prompt inbox\today.pdf
```

**Choosing pages.** An unselected page is never read, never pasted, and never
paid for:

```bash
python -m newsagent prompt inbox\today.pdf --pages 1-8,12,20-22
python -m newsagent prompt inbox\today.pdf --skip-pages 9-11,23-
python -m newsagent prompt inbox\today.pdf --skip-junk
```

`--pages` and `--skip-pages` both accept `1-4`, `7`, `10-12`, `-6` (up to 6),
`8-` (from 8 on), `odd`, `even`, and any comma- or space-separated mix.
A selector that can't be understood is refused with a specific reason rather
than quietly processing the wrong pages.

`--skip-junk` drops whatever the classifier judges to be adverts,
classifieds, listings or data tables, and tells you which pages and why.

The three flags are **filters applied in order**: `--pages` narrows,
`--skip-pages` narrows further, `--skip-junk` narrows what's left. So
`--pages 5 --skip-junk` reads page 5 *and then drops it* if it looks like
junk — if you want a specific page kept regardless of the guess, name it with
`--pages` and leave `--skip-junk` off. If the filters between them remove
everything, you're told exactly that rather than left guessing.

The same three flags work on `ingest` in the automatic workflow, where they
directly cut the bill.

This is entirely local — it extracts the text, stores the edition, and writes
paste-ready prompt files:

```
sample-edition.pdf
  edition         #1  THE MORNING CHRONICLE  2026-03-12
  pages           3 with text / 3 read
  prompts written 1
  folder          E:\newspaper-agent\prompts\edition-001
```

A 30-page paper is typically **1–2 prompt files**, because only the
column-ordered text goes in the prompt (the layout rendering is 5x larger and
mostly whitespace padding). Each file is self-contained: the instructions,
your topic list, the output format, and the page text.

### 2. Paste each one into your chat

Open `chunk-01.txt`, copy all of it, paste into Claude / ChatGPT / whatever
you use. Copy the whole reply and save it in the same folder as
`reply-01.txt`. Repeat for each chunk.

Instructions are also written into `prompts\edition-001\README.txt` so you
don't have to remember.

### 3. Load the replies

```bash
.venv\Scripts\python.exe -m newsagent load prompts\edition-001 --edition 1
```

```
reply-01.txt
  articles added  8
  topic tags      9
  full text       8 located
  topics dropped  Cricket  (not in topics.txt)
```

It reads every `reply-*.txt` in the folder and ignores the `chunk-*.txt`
prompts, so you can leave everything in one place. You can also load a single
file, or pipe from the clipboard:

```bash
python -m newsagent load reply-01.txt --edition 1
powershell -c "Get-Clipboard" | python -m newsagent load - --edition 1
```

**Loading the same reply twice is safe.** Articles are matched on page and
headline, then updated in place rather than duplicated.

### Why the reply doesn't contain the article text

The model returns an `ANCHOR` line — the article's first 10–15 words,
verbatim — instead of the whole body. The tool then slices the body out of
the page text it already holds locally.

That matters for three reasons: the reply is short enough to finish in one
response instead of being truncated, you still get the complete original text
in the database, and the stored text is the *actual* PDF text rather than the
model's re-transcription of it.

If an anchor can't be located, that article is stored with its summary and
flagged — the load report says `N not found`, and `body_source` on the row
says `unmatched`.

### It tolerates messy replies

Chat windows add preambles, markdown bold, code fences, and closing chatter.
The parser strips all of that. It also handles percentage confidences
(`95%`), `- BULLET:` list markers, articles reported out of order, replies
that got truncated mid-article (every complete article before the cut is
kept), and JSON if your model insists on returning that instead. Topic names
the model invents are discarded rather than trusted.

---

## The automatic workflow

If you'd rather not copy-paste, add an API key:

```bash
copy .env.example .env
```

Put your key in `ANTHROPIC_API_KEY`, then:

```bash
python -m newsagent ingest                    # whole inbox
python -m newsagent ingest inbox\today.pdf --max-pages 4    # cheap trial
python -m newsagent ask "anything on the RBI this week?"
python -m newsagent chat                      # conversational agent
```

`ask` and `chat` run a LangChain agent (`create_agent`, LangGraph underneath)
with nine tools over your library. These need a key even if you loaded the
articles manually — everything else does not.

Roughly **$2–4 per 30-page edition** on `claude-opus-5`. Segmentation is the
expensive half because the model echoes body text back; setting
`NEWSAGENT_SEGMENT_MODEL=claude-haiku-4-5` in `.env` cuts that by ~70% while
summaries stay on Opus.

---

## Reading what you've loaded

None of these need an API key.

### In your browser (easiest)

```bash
python -m newsagent html
```

Writes `reports\digest.html` and opens it. One self-contained file — no
server, no network, no dependencies. You can copy it to your phone or email
it to yourself and it still works.

- **Search box** filters as you type, across headlines, summaries, and the
  full article text. Press `/` to jump to it, `Esc` to clear.
- **Topic chips** filter to one interest, with counts.
- **Group by topic** on or off; ungrouped shows each article once.
- **Mark read** per article, remembered in your browser between visits
  (per-database, so two libraries don't clash). **Unread only** hides what
  you've finished.
- **Full article text** expands inline under each summary.
- Follows your system light/dark theme, and prints cleanly.

```bash
python -m newsagent html --since all        # every edition, not just 7 days
python -m newsagent html --topics-only      # only articles matching a topic
python -m newsagent html --no-open          # write the file, don't launch
python -m newsagent html -o E:\news.html    # somewhere else
```

It's a **snapshot**, not a live view — re-run it after loading a new paper.

### In the terminal

```bash
python -m newsagent digest                # one line per story, last 7 days
python -m newsagent digest --full         # every bullet + why-it-matters
python -m newsagent digest --since 1d     # just today
python -m newsagent digest --since all --all   # everything, including untagged
python -m newsagent search "repo rate"    # full-text search
python -m newsagent search "vaccine" --topic "Health & Medicine"
python -m newsagent article 42            # summary + full original text
python -m newsagent topics                # your topics + article counts
python -m newsagent editions              # which papers are loaded
python -m newsagent stats                 # counts, and anything unsummarised
```

---

## Your topics

`topics.txt` is the file you maintain. One topic per line, two formats:

```
Artificial Intelligence: AI, machine learning, LLM, OpenAI, Anthropic
Climate & Environment
```

The keywords after the colon are optional hints, and worth adding for
anything you care about. Tagging uses them twice:

1. They go into the prompt as "signals", sharpening the model's judgement.
2. A local keyword scan runs over the stored text — free, deterministic,
   matching on word boundaries and tolerating the line breaks PDF extraction
   inserts mid-phrase.

The model's job is to judge what an article is *actually about*, which is
what stops a cricket report from being filed under Banking because a bank
sponsors the tournament. Each tag records which pass found it (`chat`,
`chat+keyword`, `llm`, `llm+keyword`), a confidence, and a one-clause reason.
Tags below 0.4 confidence are dropped.

The file is re-read on every run, so **edits take effect immediately** — the
next `prompt` you generate carries the new list. Topics you delete are marked
inactive rather than erased, so tags on old articles stay meaningful.

---

## How it works

```
   PDF
    │
    ▼  extract.py    pdfplumber: detects column gutters, reads each column
    │                top-to-bottom; stores page text in the DB
    │
    ├── manual ──▶ manual.py    build_prompts()  -> paste into chat
    │                           parse_response() <- paste the reply back
    │                           slice_bodies()    -> full text from page text
    │
    └── API ─────▶ llm.py       segment_page() then summarise_article()
    │
    ▼  pipeline.py   store articles, summaries, topic tags
    ▼  db.py         SQLite + FTS5 index
    │
    ▼  agent.py      LangChain agent with 9 tools (API key only)
```

### Database

`data/articles.db`, plain SQLite — open it with any client.

| Table | Holds |
|---|---|
| `editions` | one row per PDF, deduplicated by SHA-256 |
| `pages` | verbatim extracted text of each page |
| `articles` | headline, byline, section, page, full body text, `body_source` |
| `summaries` | one-liner, bullets (JSON), entities, why-it-matters, category |
| `topics` | mirrored from `topics.txt` |
| `article_topics` | tags, with confidence, provenance, and rationale |
| `articles_fts` | FTS5 index over headline + body + summary |

`reports\digest.html` is generated from these tables, never the other way
round -- delete it any time and regenerate with `newsagent html`.

Search uses BM25 with headlines weighted 8x and summaries 4x above body text.
FTS5 operator characters in your query are stripped, so punctuation can't
produce a syntax error.

---

## Tests

```bash
.venv\Scripts\python.exe tests\test_manual.py            # 60 checks
.venv\Scripts\python.exe tests\test_pipeline_offline.py  # 28 checks
```

Both run without an API key and cost nothing. They cover extraction, prompt
chunking, response parsing (including deliberately messy and truncated
replies), body anchoring, topic validation, FTS search, digests, and
idempotency.

`scripts\make_sample_pdf.py` regenerates the sample 3-page, 4-column
newspaper. `scripts\seed_demo.py` fills a throwaway database with stub
summaries.

---

## Scanned PDFs

Many e-papers are page images with no text layer. This reads the text layer,
so it **cannot** read those — but it detects them instead of silently
producing nothing:

```
NO TEXT LAYER   pages 1, 2, 3  (needs OCR)
ERROR           no extractable text on any page -- this PDF is scanned images
```

To check yourself: open the PDF and try to select text. If you can highlight
words, it will work.

If yours is scanned, the fix is an OCR step ahead of `extract.py` (Tesseract,
or rasterising pages and reading them with a vision model). Say the word and
I'll add it.

---

## Known limits

- **Page-spanning stories.** An article marked `CONTINUED: yes` is merged
  into the one whose headline it matches. A story continuing under a
  *different* headline several pages later can still end up as two articles —
  and in the manual flow, only if both halves are in the same batch of
  replies you load.
- **Opinion vs reporting.** Columns and editorials get summarised like news,
  so a summary can read as fact when the original was argument. `category`
  usually says `Opinion`; check the full text before quoting one.
- **Summaries are lossy by design.** For anything you'd act on — a number, a
  quote, a date — read the original with `newsagent article <id>`. The full
  body text is always stored, never just the summary.
- **Segmentation quality sets the ceiling.** If the model splits a page's
  articles badly, the summaries inherit that. Spot-check `newsagent article
  <id>` against the PDF for a new paper before trusting a run. If articles
  come back split badly, re-run `prompt --include-layout` to give the model
  the visual layout too.

### Not verified

The manual workflow is tested end to end against a realistic chat reply — 88
checks pass, including the anchor slicing that recovers full article text.

The **automatic** workflow's two API calls in `llm.py` have never run against
a live key (none was available when this was built). Its schemas, prompts, and
agent graph all build correctly, but the first real `ingest` is the real test.
The manual workflow does not depend on any of that.

---

## Command Reference: All Commands

### Setup & Configuration

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# Install dependencies
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Copy environment template (for API key)
copy .env.example .env

# Edit topics.txt (your interests)
# One topic per line, optional keywords after colon
# Example: Artificial Intelligence: AI, machine learning, LLM
```

### Manual Workflow (Free - No API Key Required)

**1. Analyze pages before processing**
```bash
.venv\Scripts\python.exe -m newsagent pages "path\to\newspaper.pdf"
# Shows: article pages, sparse pages, tables, classifications
# Guides which pages to process
```

**2. Build prompts from PDF**
```bash
# All pages (auto-detects article pages)
.venv\Scripts\python.exe -m newsagent prompt "inbox\today.pdf"

# Specific pages
.venv\Scripts\python.exe -m newsagent prompt "inbox\today.pdf" --pages 1-8,12,20-22

# Skip specific pages
.venv\Scripts\python.exe -m newsagent prompt "inbox\today.pdf" --skip-pages 9-11,23-

# Auto-skip ads/classifieds/tables
.venv\Scripts\python.exe -m newsagent prompt "inbox\today.pdf" --skip-junk

# Page selector syntax
#   1-4          (range)
#   7            (single)
#   10-12        (range)
#   -6           (up to 6)
#   8-           (from 8 onward)
#   odd, even    (odd/even pages)
#   1,3,5-7,10   (mixed)
```

**3. Paste prompts into Claude chat**
- Open `prompts/edition-NNN/chunk-01.txt`
- Copy all text → paste into Claude (or ChatGPT)
- Copy full reply → save as `prompts/edition-NNN/reply-01.txt`
- Repeat for each chunk

**4. Load replies from chat**
```bash
# Load all replies from folder
.venv\Scripts\python.exe -m newsagent load "prompts\edition-001" --edition 1

# Load single reply file
.venv\Scripts\python.exe -m newsagent load "reply-01.txt" --edition 1

# Pipe from clipboard
powershell -c "Get-Clipboard" | .venv\Scripts\python.exe -m newsagent load - --edition 1
```

### Automatic Workflow (API Required - $2-4 per edition)

```bash
# Add ANTHROPIC_API_KEY to .env first

# Ingest entire PDF
.venv\Scripts\python.exe -m newsagent ingest "inbox\today.pdf"

# Ingest with page selection
.venv\Scripts\python.exe -m newsagent ingest "inbox\today.pdf" --pages 1-5,8-10

# Skip ads/classifieds
.venv\Scripts\python.exe -m newsagent ingest "inbox\today.pdf" --skip-junk

# Limit cost (process only first N pages)
.venv\Scripts\python.exe -m newsagent ingest "inbox\today.pdf" --max-pages 4

# Use cheaper model for segmentation (saves ~70%)
# Edit .env: NEWSAGENT_SEGMENT_MODEL=claude-haiku-4-5
```

### Reading & Searching

**View in browser (recommended)**
```bash
# Last 7 days (default)
.venv\Scripts\python.exe -m newsagent html

# Last 30 days
.venv\Scripts\python.exe -m newsagent html --since 30d

# Everything in database
.venv\Scripts\python.exe -m newsagent html --since all

# Only articles matching your topics
.venv\Scripts\python.exe -m newsagent html --topics-only

# Write HTML without opening browser
.venv\Scripts\python.exe -m newsagent html --no-open

# Save to specific location
.venv\Scripts\python.exe -m newsagent html -o "C:\my-news.html"
```

**View in terminal**
```bash
# One-liner per article, last 7 days
.venv\Scripts\python.exe -m newsagent digest

# Every bullet point + why-it-matters
.venv\Scripts\python.exe -m newsagent digest --full

# Last 24 hours only
.venv\Scripts\python.exe -m newsagent digest --since 1d

# Everything ever
.venv\Scripts\python.exe -m newsagent digest --since all --all

# Search by keyword
.venv\Scripts\python.exe -m newsagent search "repo rate"

# Search within a topic
.venv\Scripts\python.exe -m newsagent search "vaccine" --topic "Health & Medicine"

# Read one article in full
.venv\Scripts\python.exe -m newsagent article 42
```

### Information & Management

```bash
# List all loaded newspapers
.venv\Scripts\python.exe -m newsagent editions

# Show your topics + article counts per topic
.venv\Scripts\python.exe -m newsagent topics

# Database statistics
.venv\Scripts\python.exe -m newsagent stats

# Mark articles as read (in HTML browser)
# Click the article, checkbox appears
# Saved in browser's localStorage
```

### Advanced / LangChain Agent (API Required)

```bash
# Conversational query (needs ANTHROPIC_API_KEY)
.venv\Scripts\python.exe -m newsagent ask "any news on the RBI this week?"

# Start interactive chat session
.venv\Scripts\python.exe -m newsagent chat
```

### Testing

```bash
# Run unit tests (no API key needed)
.venv\Scripts\python.exe tests\test_manual.py            # 116 tests
.venv\Scripts\python.exe tests\test_pipeline_offline.py  # 29 tests

# Regenerate sample PDF for testing
python scripts\make_sample_pdf.py

# Seed demo database with stub articles
python scripts\seed_demo.py
```

### Common Workflows

**Process one newspaper end-to-end (manual)**
```bash
# 1. Check pages
.venv\Scripts\python.exe -m newsagent pages "inbox\today.pdf"

# 2. Build prompts (auto-selects article pages)
.venv\Scripts\python.exe -m newsagent prompt "inbox\today.pdf"

# 3. [MANUAL] Summarize chunks in Claude, save replies

# 4. Load replies
.venv\Scripts\python.exe -m newsagent load "prompts\edition-001" --edition 1

# 5. View results
.venv\Scripts\python.exe -m newsagent html --since all
```

**Process old archive automatically (API)**
```bash
# Process all PDFs in inbox folder
for %f in (inbox\*.pdf) do (
    .venv\Scripts\python.exe -m newsagent ingest "%f"
)

# View all articles
.venv\Scripts\python.exe -m newsagent html --since all
```

**Search & monitor topics**
```bash
# Search keyword
.venv\Scripts\python.exe -m newsagent search "inflation"

# Search + topic filter
.venv\Scripts\python.exe -m newsagent search "RBI" --topic "Indian Economy"

# Show topic statistics
.venv\Scripts\python.exe -m newsagent topics
```

### Troubleshooting Commands

```bash
# Verify PDF has text (not scanned images)
.venv\Scripts\python.exe -m newsagent pages "path\to\pdf.pdf"
# If all pages say "no-text", needs OCR

# Check database integrity
.venv\Scripts\python.exe -m newsagent stats

# Re-generate HTML after adding new articles
.venv\Scripts\python.exe -m newsagent html --since all --no-open

# Clear browser cache of "read" status
# Open browser DevTools → Application → LocalStorage → Clear
```

---

## Quick Examples

**Example 1: Process today's paper**
```bash
.venv\Scripts\python.exe -m newsagent prompt "inbox\today.pdf" --pages 1-8
# Summarize in Claude → save reply-01.txt
.venv\Scripts\python.exe -m newsagent load "prompts\edition-001" --edition 1
.venv\Scripts\python.exe -m newsagent html
```

**Example 2: Find all AI news this week**
```bash
.venv\Scripts\python.exe -m newsagent search "artificial intelligence" --since 7d
.venv\Scripts\python.exe -m newsagent search "AI" --topic "Artificial Intelligence"
```

**Example 3: Bulk-load archive (automatic)**
```bash
# Put all old PDFs in inbox/
.venv\Scripts\python.exe -m newsagent ingest "inbox\*.pdf" --skip-junk
.venv\Scripts\python.exe -m newsagent digest --since all --full
```
