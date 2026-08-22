#!/usr/bin/env python3
"""Append-only journal for PROGRESS.md — the project's running memory.

Every scrape (auto task, bulk backfill, or the dashboard's Scraper button), every
platform/site change, and every "this worked / this bit us" lesson lands in
``PROGRESS.md`` at the repo root so the same mistakes don't get made twice.

The file is plain Markdown with three HTML marker comments; entries are inserted
directly *after* their marker, so the newest is always on top:

    <!-- progress:platform -->   platform & pipeline changes
    <!-- progress:lessons -->    what worked / what didn't
    <!-- progress:scrapes -->    the scrape table (header row follows the marker)

Usage from Python (never raises — logging must not break a scrape)::

    from laliga.progress_log import log_scrape, log_platform, log_lesson
    log_scrape("2026-27", target="MD3 · 10 matches", saved=9, failed=1,
               duration_s=612, trigger="dashboard button")

From the shell::

    py laliga/progress_log.py scrape --season 2026-27 --saved 9 --failed 1 --note "..."
    py laliga/progress_log.py platform "Switched WhoScored fixtures URL to the archived-season form"
    py laliga/progress_log.py lesson --worked "Plain Selenium fallback survives Chrome 149"
    py laliga/progress_log.py lesson --failed "Re-running build_schedule wiped the 0-0 fixes"
    py laliga/progress_log.py show --limit 15
"""
from __future__ import annotations

import os
import sys
import time
import argparse
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_FILE = Path(os.environ.get("LALIGA_PROGRESS_FILE") or (ROOT / "PROGRESS.md"))

MARK_PLATFORM = "<!-- progress:platform -->"
MARK_LESSONS = "<!-- progress:lessons -->"
MARK_SCRAPES = "<!-- progress:scrapes -->"

PROJECT = "XLALIGA"
COMPETITION = "La Liga"

_TEMPLATE = f"""# PROGRESS — {PROJECT}

Running log of the {COMPETITION} pipeline: every scrape, every platform change, and the
lessons behind both. Newest entries first in each section. Rows under **Scrape log** are
appended automatically by `laliga/progress_log.py` (called from `run_match.py`,
`scrape_whoscored.py`, `backfill.py` and the dashboard's Scraper button) — write the
Platform and Lessons sections by hand, or with:

```
py laliga/progress_log.py platform "what changed"
py laliga/progress_log.py lesson --worked "what to keep doing"
py laliga/progress_log.py lesson --failed "what not to repeat"
```

## Platform updates & changes

{MARK_PLATFORM}

## Lessons — what worked / what didn't

{MARK_LESSONS}

## Scrape log

{MARK_SCRAPES}
| When | Season | Trigger | Target | Result | Took | Notes |
|---|---|---|---|---|---|---|
"""


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _today() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _read() -> str:
    if not PROGRESS_FILE.exists():
        PROGRESS_FILE.write_text(_TEMPLATE, encoding="utf-8")
    return PROGRESS_FILE.read_text(encoding="utf-8")


def _write(text: str) -> None:
    """Atomic-ish write so a crash mid-append can't truncate the journal."""
    tmp = PROGRESS_FILE.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, PROGRESS_FILE)


def _insert_after(marker: str, block: str, skip_lines: int = 0) -> bool:
    """Insert ``block`` after ``marker`` (plus ``skip_lines`` lines, e.g. a table header).

    Retries briefly: a scheduled task and a button-triggered job can finish together.
    """
    for attempt in range(4):
        try:
            text = _read()
            if marker not in text:                      # marker lost — re-seed the section
                text = text.rstrip() + f"\n\n{marker}\n"
            lines = text.splitlines()
            i = next(n for n, ln in enumerate(lines) if ln.strip() == marker)
            at = i + 1 + skip_lines
            lines[at:at] = block.splitlines()
            _write("\n".join(lines) + "\n")
            return True
        except Exception:
            if attempt == 3:
                return False
            time.sleep(0.4 * (attempt + 1))
    return False


def _esc(text: str) -> str:
    """Keep a note from breaking the Markdown table it sits in."""
    return " ".join(str(text or "").split()).replace("|", "/")


def _dur(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def log_scrape(season: str, target: str = "", saved: int = 0, failed: int = 0,
               skipped: int = 0, duration_s=None, trigger: str = "cli",
               note: str = "", ok: bool | None = None) -> bool:
    """Append one row to the scrape table. Returns False if the journal couldn't be written."""
    try:
        if ok is None:
            ok = failed == 0 and (saved > 0 or skipped > 0)
        bits = []
        if saved:
            bits.append(f"{saved} saved")
        if skipped:
            bits.append(f"{skipped} already had data")
        if failed:
            bits.append(f"{failed} failed")
        result = ("✅ " if ok else "⚠️ ") + (", ".join(bits) or ("done" if ok else "no data"))
        row = (f"| {_now()} | {_esc(season)} | {_esc(trigger)} | {_esc(target) or '—'} "
               f"| {result} | {_dur(duration_s)} | {_esc(note) or '—'} |")
        # skip_lines=2 → past the table's header and separator rows.
        return _insert_after(MARK_SCRAPES, row, skip_lines=2)
    except Exception:
        return False


def log_platform(summary: str, detail: str = "") -> bool:
    """Record a platform/site/pipeline change (source site redesign, model retrain, deploy…)."""
    try:
        block = f"- **{_today()}** — {' '.join(str(summary).split())}"
        if detail:
            block += "\n  " + " ".join(str(detail).split())
        return _insert_after(MARK_PLATFORM, block)
    except Exception:
        return False


def log_lesson(text: str, worked: bool = True, tag: str = "") -> bool:
    """Record a lesson. ``worked=True`` → what to keep doing; False → what not to repeat."""
    try:
        icon = "✅ **Worked**" if worked else "❌ **Didn't work**"
        label = f" _{_esc(tag)}_ —" if tag else " —"
        return _insert_after(MARK_LESSONS,
                             f"- {icon}{label} {' '.join(str(text).split())}  ({_today()})")
    except Exception:
        return False


def recent(limit: int = 20) -> str:
    """The journal's most recent scrape rows + the newest platform/lesson entries, as text."""
    try:
        text = _read()
    except Exception:
        return ""
    out = []
    for marker, title, keep in ((MARK_PLATFORM, "Platform", 6),
                                (MARK_LESSONS, "Lessons", 8),
                                (MARK_SCRAPES, "Scrapes", limit)):
        if marker not in text:
            continue
        after = text.split(marker, 1)[1].splitlines()[1:]
        rows = []
        for ln in after:
            if ln.startswith("## "):
                break
            if ln.strip():
                rows.append(ln.rstrip())
            if len(rows) >= keep + (2 if marker == MARK_SCRAPES else 0):
                break
        if rows:
            out.append(f"── {title} " + "─" * 40)
            out.extend(rows)
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=f"Append to {PROGRESS_FILE.name}.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="log a scrape run")
    s.add_argument("--season", required=True)
    s.add_argument("--target", default="")
    s.add_argument("--saved", type=int, default=0)
    s.add_argument("--failed", type=int, default=0)
    s.add_argument("--skipped", type=int, default=0)
    s.add_argument("--seconds", type=float)
    s.add_argument("--trigger", default="cli")
    s.add_argument("--note", default="")

    p = sub.add_parser("platform", help="log a platform/pipeline change")
    p.add_argument("summary")
    p.add_argument("--detail", default="")

    ls = sub.add_parser("lesson", help="log a lesson")
    g = ls.add_mutually_exclusive_group(required=True)
    g.add_argument("--worked")
    g.add_argument("--failed")
    ls.add_argument("--tag", default="")

    sh = sub.add_parser("show", help="print the newest entries")
    sh.add_argument("--limit", type=int, default=20)

    a = ap.parse_args()
    if a.cmd == "scrape":
        okw = log_scrape(a.season, a.target, a.saved, a.failed, a.skipped,
                         a.seconds, a.trigger, a.note)
    elif a.cmd == "platform":
        okw = log_platform(a.summary, a.detail)
    elif a.cmd == "lesson":
        okw = log_lesson(a.worked or a.failed, worked=bool(a.worked), tag=a.tag)
    else:
        print(recent(a.limit))
        return
    print(("Wrote to " if okw else "FAILED to write ") + str(PROGRESS_FILE))
    sys.exit(0 if okw else 1)


if __name__ == "__main__":
    main()
