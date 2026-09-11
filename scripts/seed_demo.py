"""Populate the database with stub summaries, with no API calls.

Lets you see exactly what the CLI output looks like before spending anything
on real summarisation. Point it at a throwaway database:

    POSTGRES_DB=newspaper_agent_demo python scripts/seed_demo.py

The summaries it writes are placeholders, not real model output.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from newsagent import pipeline  # noqa: E402
from webapp.config import WEB_CONFIG  # noqa: E402


def main() -> int:
    pdf = ROOT / "inbox" / "sample-edition.pdf"
    if not pdf.exists():
        from make_sample_pdf import build

        build(pdf)

    import stubs

    stubs.install(pipeline)

    report = pipeline.ingest_pdf(pdf, force=True)
    print("\n".join(report.as_lines()))
    print(f"\nseeded into postgresql://{WEB_CONFIG.postgres_host}:{WEB_CONFIG.postgres_port}/{WEB_CONFIG.postgres_db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
