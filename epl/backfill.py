#!/usr/bin/env python3
"""
Batch deep-scrape a Premier League season (FotMob + WhoScored + Understat) to layer rich data
(xG, shots, passes, dribbles, events, player stats) onto the schedule-driven dashboard.

The dashboard's standings/results/fixtures come from the token-free schedule
(``build_schedule.py``) and are complete already. This script fills in the *rich* per-match
data one game at a time via the browser scrapers — the slow, flaky part — so run it on a
machine with Chrome + the FotMob token, ideally overnight. Each match is scraped through
``run_match`` with push/WhatsApp OFF; a single ``git push`` at the end (``--push``) deploys
everything, instead of 380 commits.

Examples:
    py epl/backfill.py --season 2025-26                 # every finished, not-yet-scraped match
    py epl/backfill.py --season 2025-26 --limit 10      # just the next 10 (good for a test run)
    py epl/backfill.py --season 2025-26 --matchday 19   # only matchday 19
    py epl/backfill.py --season 2025-26 --redo          # re-scrape even already-scraped matches
    py epl/backfill.py --season 2025-26 --push          # rebuild + one git push at the end
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from epl.run_match import run_match

SCHED_DIR = _REPO_ROOT / "epl" / "schedules"
MATCH_DIR = _REPO_ROOT / "epl" / "matches"


def _already_scraped(season: str, fotmob_id: int) -> bool:
    p = MATCH_DIR / season / f"{fotmob_id}.json"
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return bool(d.get("events"))   # a real scrape has an event stream
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch deep-scrape a Premier League season.")
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--limit", type=int, help="Scrape at most N matches this run.")
    ap.add_argument("--matchday", type=int, help="Only this matchday.")
    ap.add_argument("--redo", action="store_true", help="Re-scrape matches already done.")
    ap.add_argument("--fotmob-only", action="store_true", help="Skip WhoScored (faster, no maps).")
    ap.add_argument("--delay", type=float, default=8.0, help="Seconds between matches.")
    ap.add_argument("--push", action="store_true", help="git push once at the end.")
    args = ap.parse_args()

    sched_path = SCHED_DIR / f"SCHEDULE_{args.season}.json"
    if not sched_path.exists():
        raise SystemExit(f"No schedule for {args.season}. Run: py epl/build_schedule.py --season {args.season}")
    matches = json.loads(sched_path.read_text(encoding="utf-8")).get("matches", [])

    todo = []
    for m in matches:
        if not m.get("finished"):
            continue
        if args.matchday and m.get("matchday") != args.matchday:
            continue
        if not args.redo and _already_scraped(args.season, m["fotmob_id"]):
            continue
        todo.append(m)
    if args.limit:
        todo = todo[:args.limit]

    print(f"Backfill {args.season}: {len(todo)} match(es) to scrape "
          f"(of {sum(1 for m in matches if m.get('finished'))} finished).")
    ok = fail = 0
    for i, m in enumerate(todo, 1):
        label = f"{m['home']} vs {m['away']} (MD{m.get('matchday')}, id={m['fotmob_id']})"
        print(f"\n[{i}/{len(todo)}] {label}")
        try:
            # Scrape + render + refresh local dashboard data only; no push/post per match.
            done = run_match(fotmob_id=m["fotmob_id"], season=args.season,
                             fotmob_only=args.fotmob_only, do_push=False, do_whatsapp=False)
            ok += done
            fail += (0 if done else 1)
        except Exception as exc:
            fail += 1
            print(f"   ! failed: {exc}")
        if i < len(todo):
            time.sleep(args.delay)

    print(f"\nBackfill done: {ok} ok, {fail} failed.")
    if args.push and ok:
        # One deploy for the whole batch. push_match_update clones + commits the refreshed
        # epl_dashboard/{data.js,players.js,matches_detail,database} + any new PNGs.
        try:
            from epl.git_ops import push_match_update
            # A representative PNG (any) satisfies the signature; the dashboard files are
            # what matter. Fall back to a no-PNG commit if none exist.
            pngs = list((_REPO_ROOT / "epl_png").glob("*.png"))
            png = str(pngs[0]) if pngs else None
            if png:
                push_match_update(png, match_id=f"backfill-{args.season}",
                                  commit_message=f"[EPL] backfill {args.season} ({ok} matches)")
                print("Pushed batch to GitHub.")
            else:
                print("No PNGs to push; run the dashboard build + git push manually.")
        except Exception as exc:
            print(f"Push failed (do it manually): {exc}")


if __name__ == "__main__":
    main()
