"""Step 3: fetch Sector / IndustryNew for each unique BSE scrip code via the
BSE ComHeadernew API, concurrently, caching results to disk so the run is
resumable if interrupted or rate-limited.

Usage: python fetch_sectors.py [limit]
  limit: optional cap on how many *uncached* codes to fetch this run
         (used for a small smoke-test batch before running the full set).
"""
import csv
import json
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

REFINED_PATH = r"E:\newspaper-agent\scripts\refined.csv"
CACHE_PATH = r"E:\newspaper-agent\scripts\sector_cache.json"

URL_TMPL = (
    "https://api.bseindia.com/BseIndiaAPI/api/ComHeadernew/w"
    "?quotetype=EQ&scripcode={code}&seriesid="
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json",
}


def load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    import os
    os.replace(tmp, CACHE_PATH)


def fetch_one(code):
    url = URL_TMPL.format(code=code)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return code, {
            "Sector": data.get("Sector") or "",
            "IndustryNew": data.get("IndustryNew") or "",
            "Industry": data.get("Industry") or "",
        }
    except Exception as exc:  # noqa: BLE001
        return code, {"error": str(exc)}


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    with open(REFINED_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    all_codes = sorted({r["scrip_code"] for r in rows if r["scrip_code"]})

    cache = load_cache()
    todo = [c for c in all_codes if c not in cache]
    print(f"total codes: {len(all_codes)}, already cached: {len(cache)}, todo: {len(todo)}")

    if limit:
        todo = todo[:limit]
        print(f"(smoke test) limiting this run to {len(todo)} codes")

    if not todo:
        print("nothing to do")
        return

    start = time.time()
    done = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_one, c): c for c in todo}
        for fut in as_completed(futures):
            code, result = fut.result()
            cache[code] = result
            done += 1
            if "error" in result:
                errors += 1
            if done % 200 == 0 or done == len(todo):
                save_cache(cache)
                elapsed = time.time() - start
                print(f"  {done}/{len(todo)} done, {errors} errors, {elapsed:.1f}s elapsed")

    save_cache(cache)
    print(f"finished: {done} fetched, {errors} errors, total cached: {len(cache)}")


if __name__ == "__main__":
    main()
