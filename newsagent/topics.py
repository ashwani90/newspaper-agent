"""Your topics of interest: parsing topics.txt, syncing it to the DB, and
doing the cheap deterministic keyword pass before the LLM is consulted.

topics.txt is the source of truth and is re-read on every run. Editing it
takes effect immediately -- no restart, no re-ingest.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import CONFIG
from .db import Topic


@dataclass
class TopicSpec:
    name: str
    keywords: list[str] = field(default_factory=list)

    def as_prompt_line(self) -> str:
        if self.keywords:
            return f"- {self.name} (signals: {', '.join(self.keywords)})"
        return f"- {self.name}"


def parse_topics_file(path: Path | None = None) -> list[TopicSpec]:
    """Read topics.txt.

    Accepted line formats:
        Topic Name
        Topic Name: keyword, another keyword, phrase
    Blank lines and lines starting with # are ignored.
    """
    path = path or CONFIG.topics_file
    if not path.exists():
        return []

    specs: list[TopicSpec] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, keyword_blob = line.partition(":")
        name = name.strip()
        if not name:
            continue
        keywords = [k.strip() for k in keyword_blob.split(",") if k.strip()]
        key = name.casefold()
        if key in seen:
            # Same topic listed twice: merge the keyword lists.
            for existing in specs:
                if existing.name.casefold() == key:
                    existing.keywords.extend(
                        k for k in keywords if k not in existing.keywords
                    )
                    break
            continue
        seen.add(key)
        specs.append(TopicSpec(name=name, keywords=keywords))
    return specs


def sync_topics(session: Session, specs: list[TopicSpec] | None = None) -> list[Topic]:
    """Mirror topics.txt into the topics table.

    New topics are inserted, existing ones get their keywords refreshed, and
    topics no longer in the file are marked inactive rather than deleted --
    so historical tags on old articles stay meaningful.
    """
    specs = specs if specs is not None else parse_topics_file()
    wanted = {s.name.casefold(): s for s in specs}

    existing = {t.name.casefold(): t for t in session.scalars(select(Topic)).all()}

    for key, spec in wanted.items():
        row = existing.get(key)
        if row is None:
            row = Topic(name=spec.name, keywords_json=json.dumps(spec.keywords), active=1)
            session.add(row)
            existing[key] = row
        else:
            row.name = spec.name
            row.keywords_json = json.dumps(spec.keywords)
            row.active = 1

    for key, row in existing.items():
        if key not in wanted:
            row.active = 0

    session.flush()
    return [existing[k] for k in wanted]


def _word_boundary_pattern(term: str) -> re.Pattern[str]:
    """Match a keyword or phrase on word boundaries, case-insensitively.

    Whitespace in a phrase is allowed to match any run of whitespace, which
    matters because PDF extraction happily inserts line breaks mid-phrase.
    """
    parts = [re.escape(p) for p in term.split()]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _pattern_for(term: str) -> re.Pattern[str]:
    if term not in _PATTERN_CACHE:
        _PATTERN_CACHE[term] = _word_boundary_pattern(term)
    return _PATTERN_CACHE[term]


def keyword_matches(text: str, specs: list[TopicSpec]) -> dict[str, list[str]]:
    """Free, deterministic first pass: which topics' keywords literally appear.

    Returns {topic name: [keywords that hit]}. This does not replace the LLM
    pass -- it complements it. A keyword hit is strong evidence; the absence
    of one says nothing, which is exactly why the LLM pass also runs.
    """
    hits: dict[str, list[str]] = {}
    for spec in specs:
        matched = [kw for kw in spec.keywords if _pattern_for(kw).search(text)]
        # The topic name itself counts as a keyword.
        if _pattern_for(spec.name).search(text) and spec.name not in matched:
            matched.insert(0, spec.name)
        if matched:
            hits[spec.name] = matched
    return hits
