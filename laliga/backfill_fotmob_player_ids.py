#!/usr/bin/env python3
"""One-off backfill: refetch FotMob matchDetails for every already-scraped match and
patch in ``_fotmob_player_ids`` (used for player headshots — see
PROMPT_PLAYER_PHOTOS.md) without touching anything else in the raw JSON.

Needed because this field didn't exist when older matches were scraped: scraper.py's
_fotmob_player_ids() only started being called on 2026-09-15. This is a plain HTTP
fetch per match (no browser), same endpoint scraper.py already uses.

Usage:
    py laliga/backfill_fotmob_player_ids.py                  # every season, resumable
    py laliga/backfill_fotmob_player_ids.py --season 2025-26
    py laliga/backfill_fotmob_player_ids.py --workers 4       # lower if FotMob 403s
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from laliga.scraper import fotmob_fetch_match_details, _fotmob_player_ids  # noqa: E402

MATCH_DIR = REPO_ROOT / "laliga" / "matches"


def _target_files(season: str | None):
    pattern = str(MATCH_DIR / (season or "*") / "*.json")
    for f in sorted(glob.glob(pattern)):
        if "_cache" in os.path.basename(f):
            continue
        yield Path(f)


def _process(path: Path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return "read_error", path, str(exc)

    if d.get("_fotmob_player_ids"):
        return "already_has", path, None

    if not path.stem.isdigit():
        return "not_numeric_id", path, None

    fm_data = fotmob_fetch_match_details(int(path.stem))
    if fm_data.get("_fotmob_unavailable"):
        return "fotmob_unavailable", path, None

    ids = _fotmob_player_ids(fm_data)
    if not ids:
        return "no_ids_found", path, None

    d["_fotmob_player_ids"] = ids
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return "patched", path, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", help="e.g. 2025-26; default = every season on disk")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    files = list(_target_files(args.season))
    print(f"{len(files)} match file(s) to check" + (f" (season {args.season})" if args.season else ""))

    started = time.time()
    counts: dict[str, int] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_process, f): f for f in files}
        done = 0
        for fut in as_completed(futures):
            status, path, err = fut.result()
            counts[status] = counts.get(status, 0) + 1
            if err:
                errors.append(f"{path.name}: {err}")
            done += 1
            if done % 100 == 0 or done == len(files):
                print(f"  {done}/{len(files)} — {counts}")

    took = time.time() - started
    print(f"\nDone in {took:.0f}s: {counts}")
    if errors:
        print(f"{len(errors)} read error(s), first few:")
        for e in errors[:10]:
            print(f"  {e}")


if __name__ == "__main__":
    main()
