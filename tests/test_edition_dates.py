"""Tests for reading the edition date off an e-paper filename.

Pure parsing -- needs no database, no API key and costs nothing.

    python tests/test_edition_dates.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from newsagent.extract import (  # noqa: E402
    edition_date_from_filename,
    resolve_edition_date,
)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    print("\n[1] edition_date_from_filename")
    toi = edition_date_from_filename("The_Times_Of_India_Delhi_09_10_2026.pdf")
    check("TOI filename is day first", toi == date(2026, 10, 9), str(toi))

    et = edition_date_from_filename("The_Economic_Times_Delhi_29_08_2026")
    check("bare stem works", et == date(2026, 8, 29), str(et))

    full = edition_date_from_filename(
        Path(r"E:\newspaper-agent\inbox\The_Times_Of_India_Delhi_27_09_2026.pdf")
    )
    check("full Windows path works", full == date(2026, 9, 27), str(full))

    suffixed = edition_date_from_filename("The_Times_Of_India_Delhi_08_10_2026_upd.pdf")
    check("trailing suffix after the date", suffixed == date(2026, 10, 8), str(suffixed))

    check("no date in filename gives None",
          edition_date_from_filename("wealth_edition-130372993.pdf") is None)
    check("plain name gives None", edition_date_from_filename("today.pdf") is None)
    check("impossible date gives None",
          edition_date_from_filename("Paper_31_02_2026.pdf") is None)
    check("month 13 gives None (not silently month first)",
          edition_date_from_filename("Paper_10_13_2026.pdf") is None)
    check("digits glued to a longer number are ignored",
          edition_date_from_filename("Paper_109_10_2026.pdf") is None)

    print("\n[2] resolve_edition_date")
    check("filename beats a wrong masthead guess",
          resolve_edition_date("The_Times_Of_India_Delhi_27_09_2026.pdf", date(1971, 3, 7))
          == date(2026, 9, 27))
    check("filename fills a missing masthead date",
          resolve_edition_date("The_Times_Of_India_Delhi_09_10_2026.pdf", None)
          == date(2026, 10, 9))
    check("falls back to the masthead when the filename has no date",
          resolve_edition_date("today.pdf", date(2026, 3, 12)) == date(2026, 3, 12))
    check("no date anywhere stays None", resolve_edition_date("today.pdf", None) is None)

    print(f"\n{'=' * 60}")
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print(f"  FAILED: {name}")
    print(f"{'=' * 60}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
