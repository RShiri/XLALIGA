#!/usr/bin/env python3
"""One-off (and idempotent) repair of FotMob-only shot coordinates in saved matches.

Before 2026-10-13, laliga/scraper.py's _parse_fotmob_shots() stored FotMob shotmap
coordinates raw: pitch metres (x 0-105 / y 0-68) read as if they were WhoScored's
0-100 grid, so every FotMob-only shot sat ~15 m off-centre and too close to goal.
(The away-side flip was already removed on 2026-09-13, so this does NOT unflip.)
Only matches saved from FotMob alone (no WhoScored event stream) are affected:
3 of the first 79 in 2026-27.

This rescales to WhoScored's 0-100 grid, in place, for every FotMob-sourced shot event
in a file that lacks the "_fotmob_coords" marker the fixed scraper now writes. Body part / situation tags that the old parser dropped can't be
recovered offline; a later re-scrape (backfill.py --redo-partial) brings them back.

    py laliga/repair_fotmob_shots.py                    # every season, then rebuild by hand
    py laliga/repair_fotmob_shots.py --season 2026-27 --dry-run

weekly_update.py runs it before backfill, so the next scheduled run fixes and rebuilds.
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from laliga.scraper import FOTMOB_COORDS_VERSION, fotmob_xy_to_ws  # noqa: E402

MATCH_DIR = _REPO / "laliga" / "matches"


def repair_match(d: dict) -> int:
    """Convert the FotMob-sourced events of one saved match in place.
    Returns the number of events changed (0 if already converted or nothing to do)."""
    if d.get("_fotmob_coords") == FOTMOB_COORDS_VERSION:
        return 0
    changed = 0
    for e in d.get("events") or []:
        if e.get("_source") != "fotmob":
            continue
        x, y = e.get("x"), e.get("y")
        if x is None or y is None:
            continue
        e["x"], e["y"] = fotmob_xy_to_ws(x, y)  # metres -> 0-100
        changed += 1
    d["_fotmob_coords"] = FOTMOB_COORDS_VERSION
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description="Fix FotMob-only shot coordinates in saved matches.")
    ap.add_argument("--season", help="e.g. 2026-27; default = every season folder")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    folders = [MATCH_DIR / args.season] if args.season else sorted(
        p for p in MATCH_DIR.iterdir() if p.is_dir()) if MATCH_DIR.is_dir() else []
    fixed = 0
    for folder in folders:
        for p in sorted(folder.glob("*.json")):
            if p.name.startswith("match_"):
                continue  # scraper caches, not matches
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"  ! skipped {p.name}: {exc}")
                continue
            if d.get("_fotmob_coords") == FOTMOB_COORDS_VERSION:
                continue
            n = repair_match(d)
            if not n:
                continue  # WhoScored-only file: leave it untouched (no rewrite, no diff)
            fixed += 1
            print(f"  {folder.name}/{p.name}: {n} FotMob shot(s) "
                  f"{'would be ' if args.dry_run else ''}converted")
            if not args.dry_run:
                p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(f"{fixed} match file(s) {'need' if args.dry_run else 'got'} the FotMob coordinate fix.")


if __name__ == "__main__":
    main()
