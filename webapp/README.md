# Newspaper Agent — Web App

A PostgreSQL-backed REST API + browser UI for reading articles that were
extracted and summarised by the existing CLI workflow. This replaces the
static HTML digest (`newsagent html`) with a real webapp: articles live in
Postgres, are served through a FastAPI backend, and are browsed/filtered in
a single-page UI.

The PDF extraction and summarisation workflow is **unchanged** — you still
run `newsagent prompt`, paste chunks into a chat, and `newsagent load` the
replies exactly as before. The only new step is publishing what was loaded
to this webapp with `newsagent push-web`.

```
PDF -> newsagent prompt -> [chat] -> newsagent load -> (local SQLite staging)
                                            |
                                            v
                                   newsagent push-web
                                            |
                                            v
                              POST /api/articles/bulk (this webapp)
                                            |
                                            v
                                   PostgreSQL  <-- browser UI reads this
```

## Setup

### 1. PostgreSQL

Create a database and user (adjust names/password to taste):

```sql
CREATE DATABASE newspaper_agent;
CREATE USER newspaper_user WITH PASSWORD 'your-real-password';
GRANT ALL PRIVILEGES ON DATABASE newspaper_agent TO newspaper_user;
```

### 2. Configure `.env`

Copy `.env.example` to `.env` (project root, same file the CLI reads) and
fill in your real Postgres credentials:

```bash
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=newspaper_agent
POSTGRES_USER=newspaper_user
POSTGRES_PASSWORD=your-real-password

WEBAPP_HOST=0.0.0.0
WEBAPP_PORT=8000
WEBAPP_CORS_ORIGINS=*

# Used by 'newsagent push-web' to find this webapp
NEWSAGENT_API_URL=http://localhost:8000
```

The values shipped in `.env.example` are placeholders — the app will start
against them, but will fail to connect until real credentials are supplied.

### 3. Install dependencies

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This adds `fastapi`, `uvicorn`, `psycopg2-binary`, `pydantic`, and `requests`
to the existing environment.

### 4. Run the webapp

```bash
.venv\Scripts\python.exe -m uvicorn webapp.main:app --reload --port 8000
```

Tables are created automatically on startup (`webapp/database.py:init_db`).
Open **http://localhost:8000** — that's the article browser. The JSON API
lives under `/api/*` (see below), and `/health` is a plain liveness check.

## Publishing articles from the CLI

After the usual manual workflow (`newsagent prompt` → paste → `newsagent
load`), push the loaded edition to the webapp:

```bash
# Push the most recently loaded edition
.venv\Scripts\python.exe -m newsagent push-web

# Push a specific edition
.venv\Scripts\python.exe -m newsagent push-web --edition 3

# Push to a webapp running somewhere other than NEWSAGENT_API_URL
.venv\Scripts\python.exe -m newsagent push-web --edition 3 --url http://192.168.1.10:8000
```

Re-running `push-web` for the same edition is safe: articles are matched on
`(newspaper, page, headline)` and updated in place rather than duplicated,
the same way `newsagent load` behaves locally.

## What gets stored

Each article row carries every field the browser UI filters or displays on:

| Column | Source |
|---|---|
| `newspaper_name` / `edition_date` | the PDF's edition metadata |
| `published_at` | edition date, used for date-range filtering |
| `headline`, `byline`, `section`, `page_number` | as parsed |
| `category` | from the chat-generated summary |
| `original_text` | the full article body (sliced from the page by anchor) |
| `summary_text`, `bullets`, `entities`, `why_it_matters`, `read_minutes` | the summary |
| `topics` (many-to-many, with confidence) | topic tags from `topics.txt` matching |

## API reference

| Method & path | Purpose |
|---|---|
| `POST /api/articles/bulk` | Ingest one edition's articles (used by `push-web`) |
| `GET /api/articles` | Filterable, paginated article list |
| `GET /api/articles/{id}` | One article, including full `original_text` |
| `GET /api/newspapers` | Distinct newspapers with article counts |
| `GET /api/categories` | Distinct categories with article counts |
| `GET /api/topics` | Distinct topics with article counts |
| `GET /health` | Liveness check |

### Filtering articles

`GET /api/articles` accepts:

- `newspaper` — partial match on newspaper name
- `category` — exact match
- `topic` — exact match on topic name
- `date_from`, `date_to` — ISO dates, inclusive
- `q` — search across headline, summary, and original text
- `page`, `page_size` — pagination (default 20, max 200)

```bash
curl "http://localhost:8000/api/articles?category=Technology&date_from=2026-09-01&q=AI"
```

## Known limits

- **No migrations yet.** Schema changes are applied with `create_all` on
  startup, which only adds missing tables — it will not alter an existing
  table's columns. A real schema change needs a manual `ALTER TABLE` or a
  drop/recreate of the affected table.
- **`push-web` is a separate, explicit step.** It is not wired into
  `newsagent load` automatically, so the local SQLite-based reading
  commands (`digest`, `html`, `search`, `article`) keep working exactly as
  before, independent of whether anything has been pushed to the webapp.
- **CORS is wide open by default** (`WEBAPP_CORS_ORIGINS=*`) for local
  development. Lock this down before exposing the webapp beyond localhost.
