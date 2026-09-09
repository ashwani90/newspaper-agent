"""Configuration for the web application (PostgreSQL + FastAPI).

Loaded from the same project-root .env file the CLI uses. Postgres
credentials default to obvious placeholders -- set real values in .env
before running the webapp against a real database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class WebConfig:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    api_host: str
    api_port: int
    cors_origins: list[str]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def load_web_config() -> WebConfig:
    origins = os.getenv("WEBAPP_CORS_ORIGINS", "*")
    return WebConfig(
        # --- DUMMY DEFAULTS: replace via .env with real Postgres creds ---
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", "newspaper_agent"),
        postgres_user=os.getenv("POSTGRES_USER", "newspaper_user"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "changeme_dummy_password"),
        # -------------------------------------------------------------------
        api_host=os.getenv("WEBAPP_HOST", "0.0.0.0"),
        api_port=int(os.getenv("WEBAPP_PORT", "8000")),
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
    )


WEB_CONFIG = load_web_config()
