#!/usr/bin/env python3
"""One-off backfill: refetch FotMob matchDetails for every already-scraped match and
patch in ``_fotmob_player_ids`` (player headshots — see PROMPT_PLAYER_PHOTOS.md) and
``_fotmob_shots`` (per-shot xG/xGOT — see PLAN_new_models.md item 1) without touching
anything else in the raw JSON.

Needed because neither field existed when older matches were scraped:
_fotmob_shot_xg_list() was added 2026-09-14, _fotmob_player_ids() 2026-09-15. This is
a plain HTTP fetch per match (no browser), same endpoint scraper.py already uses — one
fetch backfills both fields, since they come off the same FotMob response.

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

from laliga.scraper import (  # noqa: E402
    fotmob_fetch_match_details, _fotmob_player_ids, _fotmob_shot_xg_list,
)

MATCH_DIR = REPO_ROOT / "laliga" / "matches"


def _target_files(season: str | None):
    pattern = str(MATCH_DIR / (season or "*") / "*.json")
    for f in sorted(glob.glob(pattern)):
        if "_cache" in os.path.basename(f):
            continue
        yield Path(f)


def _fotmob_team_ids(fm_data: dict) -> tuple:
    """Same derivation as build_match_json(): header.teams[0/1].id."""
    teams = fm_data.get("header", {}).get("teams", [{}, {}])
    home = teams[0] if len(teams) > 0 else {}
    away = teams[1] if len(teams) > 1 else {}
    return home.get("id"), away.get("id")


def _process(path: Path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return "read_error", path, str(exc)

    needs_ids = not d.get("_fotmob_player_ids")
    needs_shots = not d.get("_fotmob_shots")
    if not needs_ids and not needs_shots:
        return "already_has", path, None

    if not path.stem.isdigit():
        return "not_numeric_id", path, None

    fm_data = fotmob_fetch_match_details(int(path.stem))
    if fm_data.get("_fotmob_unavailable"):
        return "fotmob_unavailable", path, None

    patched_any = False
    if needs_ids:
        ids = _fotmob_player_ids(fm_data)
        if ids:
            d["_fotmob_player_ids"] = ids
            patched_any = True
    if needs_shots:
        home_id, away_id = _fotmob_team_ids(fm_data)
        shots = _fotmob_shot_xg_list(fm_data, home_id, away_id)
        if shots:
            d["_fotmob_shots"] = shots
            patched_any = True

    if not patched_any:
        return "no_data_found", path, None

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
