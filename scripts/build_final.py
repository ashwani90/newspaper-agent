"""Step 4: merge fetched sectors back onto every row and write the final
sector-filled CSV.

Sector value written per row:
  - non_equity rows                -> 'N/A'
  - matched equity, sector found   -> BSE 'Sector' value (e.g. 'Industrials')
  - matched equity, empty sector   -> 'Unknown'
  - unmatched real company         -> 'Unknown'
"""
import csv
import json

REFINED_PATH = r"E:\newspaper-agent\scripts\refined.csv"
CACHE_PATH = r"E:\newspaper-agent\scripts\sector_cache.json"
OUT_PATH = r"E:\newspaper-agent\compaies_with_sector.csv"


def main():
    with open(CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)

    with open(REFINED_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    counts = {"N/A": 0, "sector_found": 0, "Unknown": 0}
    for row in rows:
        if row["kind"] == "non_equity":
            row["sector"] = "N/A"
            counts["N/A"] += 1
            continue

        code = row["scrip_code"]
        entry = cache.get(code) if code else None
        sector = (entry or {}).get("Sector") if entry else ""
        if sector:
            row["sector"] = sector
            counts["sector_found"] += 1
        else:
            row["sector"] = "Unknown"
            counts["Unknown"] += 1

    print("final sector counts:", counts)

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "name", "sector"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {"ticker": row["ticker"], "name": row["name"], "sector": row["sector"]}
            )
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()
