"""Central configuration, loaded from .env with sane defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _resolve(value: str) -> Path:
    """Relative paths in .env are interpreted against the project root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Config:
    api_key: str | None
    model: str
    segment_model: str
    effort: str
    topics_file: Path
    inbox: Path
    prompts_dir: Path
    reports_dir: Path
    max_pages: int


def load_config() -> Config:
    cfg = Config(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        model=os.getenv("NEWSAGENT_MODEL", "claude-opus-5"),
        segment_model=os.getenv(
            "NEWSAGENT_SEGMENT_MODEL", os.getenv("NEWSAGENT_MODEL", "claude-opus-5")
        ),
        effort=os.getenv("NEWSAGENT_EFFORT", "medium"),
        topics_file=_resolve(os.getenv("NEWSAGENT_TOPICS_FILE", "topics.txt")),
        inbox=_resolve(os.getenv("NEWSAGENT_INBOX", "inbox")),
        prompts_dir=_resolve(os.getenv("NEWSAGENT_PROMPTS", "prompts")),
        reports_dir=_resolve(os.getenv("NEWSAGENT_REPORTS", "reports")),
        max_pages=int(os.getenv("NEWSAGENT_MAX_PAGES", "0")),
    )
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    return cfg


CONFIG = load_config()
