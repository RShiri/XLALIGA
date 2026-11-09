#!/usr/bin/env python3
"""
Run weekly_update.py as soon as a fixture has finished. Fired every 15 min by the one
scheduled task from register_auto_update.ps1.

Replaces the one-task-per-fixture setup (register_fixture_tasks.ps1), whose triggers were
frozen at registration time: FotMob publishes placeholder kickoffs (Sunday 14:00 UTC) months
ahead, so all ten tasks of a round fired together, one run scraped BACKFILL_LIMIT matches and
the rest skipped on the lock. This reads the current schedule on every tick instead, and a
backlog bigger than BACKFILL_LIMIT drains over consecutive ticks.

A tick that finds nothing due only reads the schedule and exits, without touching the network.

Usage:
    py laliga/auto_update.py          # run the update if something is due
    py laliga/auto_update.py --check  # just print what is due
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHED_DIR = REPO_ROOT / "laliga" / "schedules"
MATCH_DIR = REPO_ROOT / "laliga" / "matches"

# Outside the repo: weekly_update.py commits with `git add -A`.
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "XLALIGA"
STATE_FILE = STATE_DIR / "auto_update_state.json"
LOG_FILE = STATE_DIR / "auto_update.log"

# Kickoff + this before a match is worth looking for (same margin the per-fixture tasks used).
FINISH_MARGIN = timedelta(hours=3)
# A match still not "finished" this long after kickoff is postponed, or a placeholder date.
STALE_AFTER = timedelta(hours=72)
# Same due list as the previous run means it achieved nothing (WhoScored down, unscrapable
# match). Back off, since every run also commits and pushes its refreshed schedule.
BACKOFF_FIRST = timedelta(minutes=30)
BACKOFF_MAX = timedelta(hours=24)


def _newest_season() -> str | None:
    names = sorted(p.stem.replace("SCHEDULE_", "") for p in SCHED_DIR.glob("SCHEDULE_*.json"))
    return names[-1] if names else None


def due_matches(season: str, now: datetime) -> list[dict]:
    """Matches over (or that should be) with no saved scrape, oldest first.

    Only checks that the raw file exists: reading every ~2 MB JSON on each tick is too slow.
    Matches saved from FotMob alone ("partial") are deliberately not due, because backfill.py
    skips them without --redo-partial and they would stay due forever.
    """
    path = SCHED_DIR / f"SCHEDULE_{season}.json"
    matches = json.loads(path.read_text(encoding="utf-8")).get("matches", [])
    due = []
    for m in matches:
        if (MATCH_DIR / season / f"{m['fotmob_id']}.json").exists():
            continue
        if m.get("finished"):
            due.append(m)
            continue
        try:
            age = now - datetime.fromisoformat(m["kickoff_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if FINISH_MARGIN <= age <= STALE_AFTER:  # the local schedule just hasn't heard yet
            due.append(m)
    return due


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def wait_remaining(state: dict, ids: list[int], now: datetime) -> timedelta:
    """How much longer to hold off; zero or less means run now."""
    if state.get("ids") != ids or not state.get("last_run"):
        return timedelta(0)
    wait = min(BACKOFF_FIRST * 2 ** max(state.get("attempts", 1) - 1, 0), BACKOFF_MAX)
    return datetime.fromisoformat(state["last_run"]) + wait - now


def main() -> None:
    ap = argparse.ArgumentParser(description="Update the dashboard when a fixture has finished.")
    ap.add_argument("--check", action="store_true", help="Print what is due and exit.")
    args = ap.parse_args()

    season = _newest_season()
    if not season:
        return
    now = datetime.now(timezone.utc)
    due = due_matches(season, now)
    ids = [m["fotmob_id"] for m in due]
    state = _load_state()
    wait = wait_remaining(state, ids, now)

    if args.check:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(f"{season}: {len(due)} due" + (f", backing off {wait}" if due and wait > timedelta(0) else ""))
        for m in due:
            print(f"  {m['fotmob_id']}  {m.get('date')}  {m['home']} vs {m['away']}")
        return

    if not due or wait > timedelta(0):
        return

    # Saved before the run, so one killed by Task Scheduler still counts as an attempt.
    attempts = state.get("attempts", 0) + 1 if state.get("ids") == ids else 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"ids": ids, "attempts": attempts, "last_run": now.isoformat()}),
                          encoding="utf-8")

    # The task runs under pythonw so no console pops up every 15 min; the scrape itself needs
    # the normal interpreter, with its output kept in a log since there is no console to see.
    python = Path(sys.executable).with_name("python.exe")
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write(f"\n===== {now.isoformat()} · {len(due)} due · attempt {attempts} =====\n")
        log.flush()
        subprocess.run([str(python), "laliga/weekly_update.py", "--season", season], cwd=REPO_ROOT,
                       stdout=log, stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    main()
