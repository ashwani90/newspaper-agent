"""Step 2b: for rows that didn't match the BSE equity master, decide whether
they're really a non-equity instrument we missed (name==ticker is the
telltale sign of an ETF/fund/index code, since real companies in this file
always carry a proper descriptive name) or a genuine company BSE just
doesn't cover (SME-platform / NSE-only listings) -> 'Unknown' sector.
"""
import csv
import re

MATCHED_PATH = r"E:\newspaper-agent\scripts\matched.csv"
OUT_PATH = r"E:\newspaper-agent\scripts\refined.csv"

TRUST_RE = re.compile(
    r"(TRUST$|TRUS$|\bINVIT\b|\bREIT\b|YIELD PLUS$|INFRA INVESTMENT$)",
    re.IGNORECASE,
)


def main():
    with open(MATCHED_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        if row["match"] != "unmatched":
            continue
        ticker = row["ticker"].strip()
        name = row["name"].strip()
        if name.upper() == ticker.upper() or TRUST_RE.search(name):
            row["kind"] = "non_equity"
            row["match"] = "unmatched_nonequity"
        else:
            row["kind"] = "equity_unknown"
            row["match"] = "unmatched_company"

    counts = {}
    for r in rows:
        counts[r["match"]] = counts.get(r["match"], 0) + 1
    print("refined counts:", counts)

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ticker", "name", "sector", "kind", "scrip_code", "match"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()
