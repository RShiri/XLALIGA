#!/usr/bin/env python3
"""Render the infographic PNG for every scraped match that doesn't have one yet, and
publish every PNG into the tracked ``laliga_png/`` folder the website links to.

    py laliga/render_missing.py                    # newest season
    py laliga/render_missing.py --season 2026-27
    py laliga/render_missing.py --season 2026-27 --ids 5868034 5868035

Why this exists: ``run_match.py`` (and therefore ``backfill.py``) scrapes AND renders,
but the bulk crawler ``scrape_whoscored.py`` only saves the raw match JSON — so a
matchday scraped that way shows in every table but has no PNG link in the Data tab and
no download button in the Match Centre. This script closes that gap from the raw JSONs
already on disk (no browser, no network) and is part of the Scraper button's rebuild.

It also copies PNGs from the git-ignored ``laliga/output/`` into ``laliga_png/``: the
site links ``../laliga_png/<id>.png``, so a PNG that only lives in ``output/`` works on
localhost and 404s on GitHub Pages.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MATCH_DIR = Path(os.environ.get("LALIGA_MATCH_DIR") or (ROOT / "laliga" / "matches"))
OUTPUT_DIR = ROOT / "laliga" / "output"
PUBLISH_DIR = ROOT / "laliga_png"
SCHED_DIR = ROOT / "laliga" / "schedules"


def _newest_season() -> str:
    names = sorted(p.stem.replace("SCHEDULE_", "") for p in SCHED_DIR.glob("SCHEDULE_*.json"))
    return names[-1] if names else "2025-26"


def _has_events(path: Path) -> bool:
    """A raw scrape with no shot/event data renders an empty infographic; skip those."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(d.get("shots") or d.get("events") or d.get("whoscored"))


def publish(png: Path) -> Path:
    PUBLISH_DIR.mkdir(exist_ok=True)
    dst = PUBLISH_DIR / png.name
    if not dst.exists() or dst.stat().st_mtime < png.stat().st_mtime:
        shutil.copy2(png, dst)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description="Render PNGs for scraped matches that lack one.")
    ap.add_argument("--season", default=_newest_season())
    ap.add_argument("--ids", nargs="*", help="Only these FotMob ids.")
    ap.add_argument("--force", action="store_true", help="Re-render even if a PNG exists.")
    args = ap.parse_args()

    season_dir = MATCH_DIR / args.season
    if not season_dir.is_dir():
        raise SystemExit(f"No raw matches for {args.season} under {MATCH_DIR}")

    # Batch mode: don't rebuild the dashboard after every single PNG (the caller does it once).
    os.environ["LALIGA_SKIP_DASHBOARD_REFRESH"] = "1"
    from laliga.renderer import render_wc_dashboard  # matplotlib: import lazily

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    raws = sorted(season_dir.glob("*.json"))
    if args.ids:
        want = set(str(i) for i in args.ids)
        raws = [p for p in raws if p.stem in want]

    rendered = skipped = failed = 0
    for raw in raws:
        out = OUTPUT_DIR / f"{raw.stem}.png"
        pub = PUBLISH_DIR / f"{raw.stem}.png"
        if not args.force and (out.exists() or pub.exists()):
            if out.exists():
                publish(out)
            skipped += 1
            continue
        if not _has_events(raw):
            skipped += 1
            continue
        try:
            data = json.loads(raw.read_text(encoding="utf-8"))
            render_wc_dashboard(data, str(out))
            publish(out)
            rendered += 1
            print(f"  rendered {raw.stem}.png")
        except Exception as exc:  # one bad match must not stop the batch
            failed += 1
            print(f"  ! {raw.stem}: {exc}")

    # Publish anything else that is only in output/ (older runs).
    for png in OUTPUT_DIR.glob("*.png"):
        publish(png)

    print(f"render_missing {args.season}: {rendered} rendered, {skipped} already had one or had no events, "
          f"{failed} failed; {len(list(PUBLISH_DIR.glob('*.png')))} PNGs published in laliga_png/")


if __name__ == "__main__":
    main()
