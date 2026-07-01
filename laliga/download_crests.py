#!/usr/bin/env python3
"""Download La Liga club crests from FotMob's logo CDN into team_logos/laliga/.

Every fixture in the season schedule carries the FotMob team ids (``home_id``/``away_id``),
and FotMob serves each club's crest at a stable URL keyed by that id. We pull one PNG per
club so the dashboard is self-contained (no hot-linking, works offline, and screenshots
don't stall on a slow external CDN). ``build_data.py`` prefers these local files and falls
back to the CDN URL for any club whose crest isn't present.

Usage:  py laliga/download_crests.py
"""
from __future__ import annotations

import os
import sys
import json
import glob
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
SCHED_DIR = os.path.join(_HERE, "schedules")
OUT_DIR = os.path.join(ROOT, "team_logos", "laliga")
CDN = "https://images.fotmob.com/image_resources/logo/teamlogo/{id}.png"


def _safe(name: str) -> str:
    return name.strip()


def collect_ids() -> dict[str, str]:
    ids: dict[str, str] = {}
    for f in glob.glob(os.path.join(SCHED_DIR, "SCHEDULE_*.json")):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for m in data.get("matches", []):
            if m.get("home_id"):
                ids[_safe(m["home"])] = str(m["home_id"])
            if m.get("away_id"):
                ids[_safe(m["away"])] = str(m["away_id"])
    return ids


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    ids = collect_ids()
    if not ids:
        raise SystemExit("No team ids found — run laliga/build_schedule.py first.")
    ok = skip = fail = 0
    for team, tid in sorted(ids.items()):
        dest = os.path.join(OUT_DIR, team + ".png")
        if os.path.exists(dest) and os.path.getsize(dest) > 500:
            skip += 1
            continue
        try:
            req = urllib.request.Request(CDN.format(id=tid), headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=25).read()
            if not data.startswith(b"\x89PNG"):
                raise ValueError("not a PNG")
            with open(dest, "wb") as fh:
                fh.write(data)
            ok += 1
            print(f"  + {team} ({tid})")
        except Exception as exc:
            fail += 1
            print(f"  ! {team} ({tid}) failed: {exc}")
    print(f"\nDone: {ok} downloaded, {skip} already present, {fail} failed → {OUT_DIR}")


if __name__ == "__main__":
    main()
