#!/usr/bin/env python3
"""
One weekly maintenance run: refresh fixtures/results, scrape whatever's newly
finished, rebuild the dashboard, and push — meant to be fired by a recurring
Windows Scheduled Task (see register_weekly_task.ps1) rather than the
per-match tasks in register_tasks.ps1.

Steps (season auto-detected as the newest SCHEDULE_*.json on disk, i.e.
whichever season is currently in progress):
    1. laliga/build_schedule.py --season <season>   (FotMob sweep, no browser)
    2. laliga/backfill.py --season <season>         (WhoScored scrape of any
       newly-finished match + a full dashboard rebuild — see backfill.py)
    3. git add -A && git commit && git push          (only if something changed;
       plain git, NOT git_ops/XWORLDCUPTWIT_REPO — see CLAUDE.md gotchas on why)

Every run appends one line to PROGRESS.md (via log_platform) so a scheduled
run that silently failed doesn't go unnoticed forever.

Usage:
    py laliga/weekly_update.py                  # auto-detect season
    py laliga/weekly_update.py --season 2026-27  # force a season
    py laliga/weekly_update.py --no-push         # rebuild locally, skip git push
"""
from __future__ import annotations

import sys
import time
import argparse
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHED_DIR = REPO_ROOT / "laliga" / "schedules"

sys.path.insert(0, str(REPO_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from laliga.progress_log import log_platform  # noqa: E402

PY = sys.executable or "py"


def _newest_season() -> str:
    names = sorted(p.stem.replace("SCHEDULE_", "") for p in SCHED_DIR.glob("SCHEDULE_*.json"))
    if not names:
        raise SystemExit("No SCHEDULE_*.json found under laliga/schedules — nothing to update.")
    return names[-1]


def _run(argv: list[str]) -> int:
    print(f"\n$ {' '.join(argv)}")
    proc = subprocess.run(argv, cwd=REPO_ROOT)
    return proc.returncode


def _git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly La Liga fixtures/results/scrape/push run.")
    ap.add_argument("--season", help="e.g. 2026-27; default = newest schedule on disk")
    ap.add_argument("--no-push", action="store_true", help="Rebuild only, skip git push.")
    args = ap.parse_args()

    season = args.season or _newest_season()
    started = time.time()
    print(f"── Weekly update — season {season} ──")

    rc1 = _run([PY, "laliga/build_schedule.py", "--season", season])
    rc2 = _run([PY, "laliga/backfill.py", "--season", season])

    pushed = False
    push_note = ""
    if not args.no_push:
        rc, status = _git("status", "--porcelain")
        if status.strip():
            _git("add", "-A")
            rc, msg = _git("commit", "-m", f"[LaLiga] weekly update ({season})")
            if rc == 0:
                rc, out = _git("push")
                if rc == 0:
                    pushed = True
                    print("Pushed to GitHub.")
                else:
                    push_note = f"git push failed: {out[:200]}"
                    print(f"! {push_note}")
            else:
                push_note = f"git commit failed: {msg[:200]}"
                print(f"! {push_note}")
        else:
            print("Nothing changed — skipping commit/push.")
    else:
        print("--no-push given — skipping deploy.")

    took = time.time() - started
    ok = rc1 == 0 and rc2 == 0 and not push_note
    summary = (f"Weekly update ({season}): schedule rc={rc1}, backfill rc={rc2}, "
               f"pushed={pushed}" + (f", {push_note}" if push_note else "") +
               f", took {int(took)}s")
    print(f"\n{summary}")
    log_platform(summary)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
