"""Step 1: classify each row of compaies.csv as equity-like or non-equity.

Non-equity rows (funds, ETFs, government securities, corporate bonds/NCDs,
treasury bills) get sector='N/A' per the user's instruction. Everything else
is a candidate for a real sector lookup.
"""
import csv
import re

IN_PATH = r"E:\newspaper-agent\compaies.csv"
OUT_PATH = r"E:\newspaper-agent\scripts\classified.csv"

NON_EQUITY_NAME_RE = re.compile(
    r"(mutual fund|\bETF\b|sovereign gold bond|\bSGB\b|government\b.*trust|"
    r"\bREIT\b|\bInvIT\b|Infrastructure Investment Trust|Investment Trust\b)",
    re.IGNORECASE,
)
# tickers that are clearly debt/bond/gilt/t-bill instrument codes, not company shares
NON_EQUITY_TICKER_RE = re.compile(
    r"(%|-NCD$|-PVT$|-MB$|-MBGB$|GOI\d|GOI$|^\d*TB\d|^\d*T\d{6}|SGB|"
    r"SDL\d|CG\d{4}|-PTC$|PERP$|-PVT|^GS\d|CENTRAL GOVT)",
    re.IGNORECASE,
)
# ticker looks like a pure BSE numeric scrip code (5-6 digits)
BSE_CODE_RE = re.compile(r"^\d{5,6}$")


def classify(ticker: str, name: str) -> str:
    if NON_EQUITY_NAME_RE.search(name):
        return "non_equity"
    if NON_EQUITY_TICKER_RE.search(ticker):
        return "non_equity"
    # BSE debt/gov-security scrip codes live in the 700000-999999 band;
    # real equity scrip codes are 500000s-599999 range (plus a few legacy 100000s).
    if BSE_CODE_RE.match(ticker):
        code = int(ticker)
        if code >= 700000 or code < 500000:
            return "non_equity"
        return "bse_equity"
    return "nse_equity"


def main():
    rows = []
    with open(IN_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            name = (row.get("name") or "").strip()
            kind = classify(ticker, name)
            rows.append({"ticker": ticker, "name": name, "sector": "", "kind": kind})

    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print("classification counts:", counts)

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "name", "sector", "kind"])
        writer.writeheader()
        writer.writerows(rows)
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()
