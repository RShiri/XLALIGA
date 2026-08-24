#!/usr/bin/env python3
"""
Batch deep-scrape a La Liga season (FotMob + WhoScored + Understat) to layer rich data
(xG, shots, passes, dribbles, events, player stats) onto the schedule-driven dashboard.

The dashboard's standings/results/fixtures come from the token-free schedule
(``build_schedule.py``) and are complete already. This script fills in the *rich* per-match
data one game at a time via the browser scrapers — the slow, flaky part — so run it on a
machine with Chrome + the FotMob token, ideally overnight. Each match is scraped through
``run_match`` with push/WhatsApp OFF; a single ``git push`` at the end (``--push``) deploys
everything, instead of 380 commits.

Examples:
    py laliga/backfill.py --season 2025-26                 # every finished, not-yet-scraped match
    py laliga/backfill.py --season 2025-26 --limit 10      # just the next 10 (good for a test run)
    py laliga/backfill.py --season 2025-26 --matchday 19   # only matchday 19
    py laliga/backfill.py --season 2025-26 --redo          # re-scrape even already-scraped matches
    py laliga/backfill.py --season 2025-26 --push          # rebuild + one git push at the end
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

from laliga.run_match import run_match
from laliga.progress_log import log_scrape

SCHED_DIR = _REPO_ROOT / "laliga" / "schedules"
MATCH_DIR = _REPO_ROOT / "laliga" / "matches"


def _scrape_state(season: str, fotmob_id: int) -> str:
    """'none' | 'partial' | 'full'.

    'partial' = the match was saved, but from FotMob shots alone — WhoScored (the event
    stream behind the pass/dribble maps, lineups and coordinates) didn't come through.
    Worth distinguishing: a partial match must not look "done" forever just because a
    browser failed once.
    """
    p = MATCH_DIR / season / f"{fotmob_id}.json"
    if not p.exists():
        return "none"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return "none"
    if not d.get("events"):
        return "none"
    return "full" if "whoscored" in (d.get("_sources") or []) else "partial"


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch deep-scrape a La Liga season.")
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--limit", type=int, help="Scrape at most N matches this run.")
    ap.add_argument("--matchday", type=int, help="Only this matchday.")
    ap.add_argument("--redo", action="store_true", help="Re-scrape matches already done.")
    ap.add_argument("--redo-partial", action="store_true",
                    help="Also re-scrape matches saved with FotMob shots only (no WhoScored "
                         "event stream) — use once Chrome/WhoScored is working again.")
    ap.add_argument("--fotmob-only", action="store_true", help="Skip WhoScored (faster, no maps).")
    ap.add_argument("--delay", type=float, default=8.0, help="Seconds between matches.")
    ap.add_argument("--push", action="store_true", help="git push once at the end.")
    args = ap.parse_args()

    sched_path = SCHED_DIR / f"SCHEDULE_{args.season}.json"
    if not sched_path.exists():
        raise SystemExit(f"No schedule for {args.season}. Run: py laliga/build_schedule.py --season {args.season}")
    matches = json.loads(sched_path.read_text(encoding="utf-8")).get("matches", [])

    todo, partial = [], []
    for m in matches:
        if not m.get("finished"):
            continue
        if args.matchday and m.get("matchday") != args.matchday:
            continue
        state = _scrape_state(args.season, m["fotmob_id"])
        if state == "full" and not args.redo:
            continue
        if state == "partial" and not (args.redo or args.redo_partial):
            partial.append(m)
            continue
        todo.append(m)
    if args.limit:
        todo = todo[:args.limit]

    print(f"Backfill {args.season}: {len(todo)} match(es) to scrape "
          f"(of {sum(1 for m in matches if m.get('finished'))} finished).")
    if partial:
        print(f"  {len(partial)} match(es) have FotMob shots but no WhoScored event stream "
              f"(no pass/dribble maps or lineups). Re-run with --redo-partial once WhoScored "
              f"loads again to fill them in.")
    started = time.time()
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
    still_partial = sum(1 for m in matches if m.get("finished")
                        and _scrape_state(args.season, m["fotmob_id"]) == "partial")
    if still_partial:
        print(f"{still_partial} match(es) still hold FotMob data only — "
              f"'py laliga/backfill.py --season {args.season} --redo-partial' retries just those.")
    target = f"{len(todo)} match(es)"
    if args.matchday:
        target = f"matchday {args.matchday} · {target}"
    log_scrape(season=args.season, target=target, saved=ok, failed=fail,
               duration_s=time.time() - started, trigger="backfill.py",
               note=("--redo" if args.redo else "") + (" --fotmob-only" if args.fotmob_only else ""))
    if args.push and ok:
        # One deploy for the whole batch. push_match_update clones + commits the refreshed
        # laliga_dashboard/{data.js,players.js,matches_detail,database} + any new PNGs.
        try:
            from laliga.git_ops import push_match_update
            # A representative PNG (any) satisfies the signature; the dashboard files are
            # what matter. Fall back to a no-PNG commit if none exist.
            pngs = list((_REPO_ROOT / "laliga_png").glob("*.png"))
            png = str(pngs[0]) if pngs else None
            if png:
                push_match_update(png, match_id=f"backfill-{args.season}",
                                  commit_message=f"[LaLiga] backfill {args.season} ({ok} matches)")
                print("Pushed batch to GitHub.")
            else:
                print("No PNGs to push; run the dashboard build + git push manually.")
        except Exception as exc:
            print(f"Push failed (do it manually): {exc}")


if __name__ == "__main__":
    main()
