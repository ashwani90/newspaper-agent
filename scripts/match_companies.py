"""Step 2: match each classified row against the BSE equity master to find
its scrip code, so we can later fetch one Sector per unique scrip code
instead of once per row (many companies appear twice: once by numeric BSE
code, once by NSE-style symbol).
"""
import csv
import json
import re

CLASSIFIED_PATH = r"E:\newspaper-agent\scripts\classified.csv"
MASTER_PATH = r"E:\newspaper-agent\scripts\bse_equity_master.json"
OUT_PATH = r"E:\newspaper-agent\scripts\matched.csv"

_SUFFIX_RE = re.compile(
    r"\b(LIMITED|LTD\.?|LLP|PVT\.?|PRIVATE|CO\.?|COMPANY|INDIA|"
    r"\(INDIA\)|\(I\)\.?|INC\.?)\b"
)
_PUNCT_RE = re.compile(r"[^A-Z0-9]+")


def norm_name(name: str) -> str:
    n = name.upper()
    n = _SUFFIX_RE.sub(" ", n)
    n = _PUNCT_RE.sub(" ", n)
    return " ".join(n.split())


def main():
    with open(MASTER_PATH, encoding="utf-8") as f:
        master = json.load(f)

    by_code = {}
    by_symbol = {}
    by_name = {}
    for rec in master:
        code = (rec.get("SCRIP_CD") or "").strip()
        symbol = (rec.get("scrip_id") or "").strip().upper()
        issuer = rec.get("Issuer_Name") or rec.get("Scrip_Name") or ""
        if code:
            by_code.setdefault(code, rec)
        if symbol:
            by_symbol.setdefault(symbol, rec)
        key = norm_name(issuer)
        if key:
            by_name.setdefault(key, rec)

    rows = []
    with open(CLASSIFIED_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    match_kind_counts = {}
    for row in rows:
        if row["kind"] == "non_equity":
            row["scrip_code"] = ""
            row["match"] = "non_equity"
            match_kind_counts["non_equity"] = match_kind_counts.get("non_equity", 0) + 1
            continue

        ticker = row["ticker"].strip().upper()
        name = row["name"].strip()
        rec = None
        how = None

        if ticker in by_code:
            rec = by_code[ticker]
            how = "code"
        elif ticker in by_symbol:
            rec = by_symbol[ticker]
            how = "symbol"
        else:
            key = norm_name(name)
            if key in by_name:
                rec = by_name[key]
                how = "name"

        if rec:
            row["scrip_code"] = rec.get("SCRIP_CD") or ""
            row["match"] = how
        else:
            row["scrip_code"] = ""
            row["match"] = "unmatched"
        match_kind_counts[row["match"]] = match_kind_counts.get(row["match"], 0) + 1

    print("match counts:", match_kind_counts)

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ticker", "name", "sector", "kind", "scrip_code", "match"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()
