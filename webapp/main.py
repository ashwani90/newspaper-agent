"""FastAPI application entrypoint.

Run with:
    uvicorn webapp.main:app --reload

Serves the JSON API under /api/* and the browser UI (webapp/static/) at /.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import WEB_CONFIG
from .database import init_db
from .routers import articles

STATIC_DIR = Path(__file__).resolve().parent / "static"


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


# Mounted last so it does not shadow /api/* or /health.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
