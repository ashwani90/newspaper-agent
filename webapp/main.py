"""FastAPI application entrypoint.

Run with:
    uvicorn webapp.main:app --reload

Serves the JSON API under /api/* and the browser UI (webapp/static/) at /.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .config import WEB_CONFIG
from .database import init_db
from .routers import articles

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """Short hash of app.js + style.css, used to cache-bust index.html's links.

    Computed once at import time. uvicorn --reload restarts the whole
    process on any file change, so this is always in sync with what is on
    disk -- no manual version bump needed. Belt-and-suspenders alongside
    NoCacheStaticFiles below: even a client or proxy that ignores
    Cache-Control still can't reuse a stale app.js/style.css, because a
    changed file produces an entirely new URL it has never cached.
    """
    digest = hashlib.sha256()
    for name in ("app.js", "style.css"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that always revalidates with the server.

    Browsers are otherwise free to keep serving an old index.html/app.js/
    style.css from disk cache indefinitely. Since these three files are
    versioned together (an old app.js can reference elements a newer
    index.html no longer has, or vice versa), a stale one silently breaking
    the page is worse than the extra round trip this costs -- ETags still
    make an unchanged file a fast 304, not a full re-download.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Newspaper Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=WEB_CONFIG.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(articles.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    """Serve index.html with cache-busted asset links (see ASSET_VERSION)."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="style.css"', f'href="style.css?v={ASSET_VERSION}"')
    html = html.replace('src="app.js"', f'src="app.js?v={ASSET_VERSION}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/tags", include_in_schema=False)
def tags_page() -> HTMLResponse:
    """Ranked list of topics by article count -- see webapp/static/tags.html."""
    html = (STATIC_DIR / "tags.html").read_text(encoding="utf-8")
    html = html.replace('href="style.css"', f'href="style.css?v={ASSET_VERSION}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


# Mounted last so it does not shadow /, /api/*, or /health.
app.mount("/", NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="static")
