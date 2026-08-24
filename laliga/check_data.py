#!/usr/bin/env python3
"""What is actually in the scraped match files — sources, events, xG, lineups.

Answers "where did this match's data come from?" without opening JSON by hand:

    py laliga/check_data.py                 # newest season
    py laliga/check_data.py --season 2025-26
    py laliga/check_data.py --season 2026-27 --detail   # one line per match
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from pathlib import Path
from collections import Counter

_REPO = Path(__file__).resolve().parents[1]
MATCH_DIR = Path(os.environ.get("LALIGA_MATCH_DIR") or (_REPO / "laliga" / "matches"))
SCHED_DIR = _REPO / "laliga" / "schedules"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _newest_season() -> str:
    names = sorted(p.stem.replace("SCHEDULE_", "") for p in SCHED_DIR.glob("SCHEDULE_*.json"))
    return names[-1] if names else "2025-26"


def main() -> None:
    ap = argparse.ArgumentParser(description="Report what each scraped match actually contains.")
    ap.add_argument("--season", default=_newest_season())
    ap.add_argument("--detail", action="store_true", help="one line per match")
    args = ap.parse_args()

    folder = MATCH_DIR / args.season
    files = sorted(p for p in folder.glob("*.json") if not p.name.startswith("match_")) \
        if folder.is_dir() else []
    if not files:
        print(f"No scraped matches in {folder}")
        return

    combos, rows = Counter(), []
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append((p.stem, "unreadable", 0, None, 0, str(exc)[:40]))
            continue
        srcs = d.get("_sources") or []
        events = len(d.get("events") or [])
        xg = (d.get("match_stats") or {}).get("xg")
        xg_txt = (f"{xg.get('home')}-{xg.get('away')}" if isinstance(xg, dict)
                  else ("yes" if xg else "—"))
        players = len((d.get("home") or {}).get("players") or []) + \
                  len((d.get("away") or {}).get("players") or [])
        combos["+".join(srcs) or "(none)"] += 1
        name = f"{(d.get('home') or {}).get('name','?')} v {(d.get('away') or {}).get('name','?')}"
        rows.append((p.stem, "+".join(srcs) or "(none)", events, xg_txt, players, name))

    print(f"{args.season}: {len(files)} scraped match(es) in {folder}\n")
    print("  source combinations:")
    for combo, n in combos.most_common():
        note = ""
        if "whoscored" not in combo:
            note = "  ← no event stream: no maps, no lineups, no model xG"
        print(f"    {n:>3} x  {combo}{note}")

    with_events = sum(1 for r in rows if isinstance(r[2], int) and r[2] > 100)
    print(f"\n  {with_events}/{len(rows)} have a full event stream (>100 events)")

    if args.detail:
        print("\n  id           sources                     events   xG        players  match")
        for stem, combo, events, xg_txt, players, name in rows:
            print(f"  {stem:<12} {combo:<27} {events:>6}   {str(xg_txt):<9} {players:>7}  {name}")
    else:
        print("  (--detail lists every match)")


if __name__ == "__main__":
    main()
