---
name: newspaper
description: Read a newspaper PDF end to end - extract the chosen pages, summarise every article, tag them against topics.txt, store them in SQLite and open an HTML reading page. Use when the user names a newspaper or e-paper PDF and wants it summarised, digested, or "read for me", optionally with page numbers (e.g. "summarise today.pdf pages 1-8,12"). Also use for "what's in the paper today", loading a paper into the news library, or regenerating the reading page. Costs nothing - you are the summarising model, so no API key is used.
---

# Read a newspaper PDF in one go

You are the summarising model for the `newspaper-agent` tool, which lives at
`E:\newspaper-agent`. (If the project moves, edit that path in this file.)
Its manual workflow exists precisely so that a chat model — you — does the
summarising instead of a paid API call. Run the whole loop yourself.

Always use the venv interpreter, never bare `python`:

```
E:\newspaper-agent\.venv\Scripts\python.exe -m newsagent <command>
```

## Arguments

The user's arguments give the PDF and, optionally, the pages:

- `today.pdf 1-8,12` → PDF `today.pdf`, pages `1-8,12`
- `E:\papers\toi-2026-03-12.pdf` → that PDF, pages not yet chosen
- `pages 1-4 of hindu.pdf` → same thing in prose; read the intent

Resolve the PDF path: use it as given if it exists, otherwise look in
`E:\newspaper-agent\inbox\`. If the file does not exist, say so, list what is
in `inbox\`, and stop — do not guess at a different paper.

Page selectors accept `1-4`, `7`, `10-12`, `-6`, `8-`, `odd`, `even`, and
comma- or space-separated mixes.

## Steps

### 1. If the user gave no pages, look before choosing

```
... -m newsagent pages "<pdf>"
```

This prints a per-page verdict (`articles`, `notices`, `tabular`, `sparse`,
`no-text`) with the reason. Show the user the table's conclusion, then
proceed with the pages it reports as `articles`. Say which pages you are
processing and which you are leaving out, in one line. Do not stop to ask —
this is a reversible, zero-cost read — but if the verdict is all `no-text`,
report that the PDF is scanned images needing OCR and stop.

If the user did give pages, use exactly those and skip this step.

### 2. Build the prompts

```
... -m newsagent prompt "<pdf>" --pages <spec>
```

Note the edition id and the prompt folder from the output (e.g.
`prompts\edition-003`). If a page selection was not possible to determine,
`--skip-junk` is a reasonable fallback.

### 3. Answer each prompt yourself — this is the actual work

For **each** `chunk-NN.txt` in the prompt folder, in order:

1. Read the file. It contains full instructions, the user's topic list, the
   output format, and the page text.
2. Follow those instructions exactly and produce the article blocks.
3. Write your answer to `reply-NN.txt` **in the same folder**, with the
   number matching its chunk. Use the Write tool — never `echo` or a heredoc,
   because the text contains quotes and `#` markers that shells mangle.

Rules that matter more than the rest:

- **`ANCHOR` must be copied verbatim** from the page text — the first 10-15
  words of the article's body, not the headline or byline. The tool locates
  the full article text by matching this string against the PDF text. If you
  paraphrase it, that article loses its full text. Copy, do not retype from
  memory.
- **Summarise only from the page text in front of you.** Never add a figure,
  name, or date from your own knowledge, and never state as fact something
  the article attributes or hedges. If the page text is garbled, say less.
- **Use topic names exactly as spelled in the prompt's topic list**, and only
  those. An invented topic is discarded on load. Match on what an article is
  genuinely *about* — a keyword appearing in passing is not a match.
- **Leave out everything that is not an article**: adverts, classifieds,
  public notices, data tables, puzzles, listings, mastheads and running
  headers, standalone photo captions. If a whole page has none, write exactly
  `NO ARTICLES ON THIS PAGE` as that reply.
- Write the blocks and nothing else — no preamble, no closing remarks. (The
  parser tolerates them, but they are noise.)

### 4. Load the replies

```
... -m newsagent load "<prompt folder>" --edition <id>
```

**Read the report and act on it:**

- `full text N located` / `M not found` — every `not found` means one of your
  anchors was not verbatim. If any are unmatched, open the chunk file, fix
  those `ANCHOR` lines against the real page text, and load again (loading
  twice is safe; articles update in place).
- `topics dropped ...` — you invented a topic name. Correct it and reload.
- `note ...` warnings — read them; they name the article involved.

### 5. Open the reading page

```
... -m newsagent html --since all
```

## Report back

Keep it to a few lines: how many articles, how many matched the user's
topics, which pages you skipped and why, anything that failed, and the path
to the HTML page. Then give the terminal alternative in one line:

```
E:\newspaper-agent\.venv\Scripts\python.exe -m newsagent digest --since all --full
```

Do not paste the summaries into the chat — they are in the HTML page and the
database. Mention any article whose full text could not be located.

## Notes

- Topics live in `E:\newspaper-agent\topics.txt`, one per line, optionally
  `Topic: keyword, keyword`. It is re-read every run. You may show the user
  the exact line to add, but do not edit that file unless they ask.
- Everything here is free and local. Do not use `newsagent ingest`, `ask`, or
  `chat` — those call the paid API. `prompt`, `load`, `pages`, `html`,
  `digest`, `search` and `article` need no key.
- Re-running on the same PDF is safe: editions dedupe by file hash and
  articles update in place rather than duplicating.
- For a paper with several chunks, do them one at a time and load once at the
  end, or load after each — both work.

## Example Invocation

User says: "Process today.pdf pages 1-10,15-20"

```bash
# 1. User gave pages, skip the pages command
# 2. Build prompts
.venv\Scripts\python.exe -m newsagent prompt "inbox\today.pdf" --pages 1-10,15-20
# Output shows: prompts\edition-003, chunk-01.txt, chunk-02.txt, etc.

# 3. Read chunk-01.txt → Summarize in Claude → Save reply-01.txt
# Read chunk-02.txt → Summarize in Claude → Save reply-02.txt

# 4. Load all replies
.venv\Scripts\python.exe -m newsagent load "prompts\edition-003" --edition 3

# 5. Generate reading page
.venv\Scripts\python.exe -m newsagent html --since all

# Report: "Processed today.pdf, pages 1-10,15-20. Found 24 articles. 8 matched your topics. 
# Full text located for 20 articles. HTML page: E:\newspaper-agent\reports\digest.html
# Terminal view: newsagent digest --since all --full"
```
