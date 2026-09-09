# Newspaper Agent Skill

Claude Code skill for end-to-end newspaper PDF processing using `newspaper-agent`.

## Files

- **`newspaper/SKILL.md`** — The skill definition for Claude Code

## How to Use

### Option 1: From Claude Code (Recommended)

This skill is registered in Claude Code. To use it:

```
/newspaper today.pdf
/newspaper today.pdf 1-8,12
/newspaper today.pdf pages 1-10
```

The skill runs the whole thing in one go:
1. Classifies pages (if needed)
2. Builds prompts
3. Walks you through summarizing each chunk
4. Loads results into local SQLite
5. Opens HTML reading page
6. Pushes the edition to the PostgreSQL-backed webapp (`newsagent push-web`)

Step 6 runs automatically every time — you don't need to ask for it
separately. It only fails softly (reported, not blocking) if the webapp
isn't running or Postgres isn't configured yet.

### Option 2: Direct Command Line

If you want to run commands without the skill:

```bash
cd E:\newspaper-agent

# Classify pages
.venv\Scripts\python.exe -m newsagent pages "path\to\paper.pdf"

# Build prompts
.venv\Scripts\python.exe -m newsagent prompt "path\to\paper.pdf" --pages 1-8,12

# Load summarized articles
.venv\Scripts\python.exe -m newsagent load "prompts\edition-001" --edition 1

# View results
.venv\Scripts\python.exe -m newsagent html --since all

# Publish to the PostgreSQL-backed webapp
.venv\Scripts\python.exe -m newsagent push-web --edition 1
```

See **[README.md](../README.md)** for complete command reference.

## Skill Workflow

1. **Pages** (optional)
   - If user didn't specify pages, run `newsagent pages` to classify each page
   - Show results and proceed with article pages
   - Skip if user gave explicit page selection

2. **Prompt**
   - Run `newsagent prompt` with selected pages
   - Returns chunk files to summarize

3. **Summarize** (Manual - You Do This)
   - Read each `chunk-NN.txt`
   - Summarize articles in Claude chat
   - Save reply as `reply-NN.txt`
   - Repeat for each chunk

4. **Load**
   - Run `newsagent load` with all replies
   - Reports on anchor mismatches and topic tags
   - Articles now in database

5. **Read**
   - Run `newsagent html --since all`
   - Opens interactive reading page in browser

6. **Publish**
   - Run `newsagent push-web --edition <id>`
   - Sends the edition to the PostgreSQL-backed webapp via its API
   - Always attempted; a failure here (webapp not running, bad Postgres
     creds) is reported but does not undo steps 1-5

## Key Rules

- **ANCHOR must be verbatim** — first 10-15 words of article body from page text
- **Use exact topic names** from your `topics.txt` file
- **Exclude non-articles** — ads, classifieds, tables, puzzles, page furniture
- **One page with no articles?** Write exactly: `NO ARTICLES ON THIS PAGE`

## Cost

**Free.** You are the summarizing model, so no API key required.

Compare to automatic mode: $2-4 per 30-page edition with API.

## Tips

- Start with `--skip-junk` if unsure which pages have articles
- Use page ranges: `1-5,10-15,20-` (from 20 onward)
- Re-run same PDF safely — articles update in place, no duplicates
- View old articles: `newsagent html --since all`
- Search: `newsagent search "keyword"`

## For Claude Code

The skill path is:

```
C:\Users\ashwa\.claude\skills\newspaper\SKILL.md
```

If moved, update the path in the skill frontmatter (top of file).
