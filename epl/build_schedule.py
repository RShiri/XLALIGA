#!/usr/bin/env python3
"""
Build a Premier League season schedule (fixtures + results) from FotMob's token-free XML feed.

FotMob's ``api.fotmob.com/matches?date=YYYYMMDD`` endpoint returns, per day, every
league's matches with real team names, team ids, kick-off time, matchday (``stage``)
and — for finished games — the final score (``Status='F'``, ``hScore``/``aScore``).
No token, no browser required. This module sweeps every date in a season's window,
keeps only the Premier League (FotMob league id 47, name "Premier League"),
de-duplicates by match id and writes ``epl/schedules/SCHEDULE_<season>.json``.

That JSON is the spine of the dashboard: the standings table, the results/fixtures
list and the matchday grouping are all derived from it. The rich per-match data
(xG, shot/pass/dribble maps, player stats) is layered on later by the browser
scrapers (see ``epl/run_match.py`` / ``epl/backfill.py``); this file needs
none of that.

Usage:
    py epl/build_schedule.py                      # default season 2025-26
    py epl/build_schedule.py --season 2026-27     # once FotMob lists the fixtures
    py epl/build_schedule.py --season 2025-26 --start 2025-08-01 --end 2026-06-15
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# The Windows console here is a legacy codepage (cp1255); force UTF-8 so any accented
# club names and glyphs print instead of crashing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
SCHED_DIR = _HERE / "schedules"

# FotMob league id for the Premier League (override with EPL_FOTMOB_LEAGUE_ID). The XML
# feed tags the English top flight as name "Premier League" id 47; the second tier is the
# EFL Championship (id 48), which the id/name filter naturally excludes.
FOTMOB_LEAGUE_ID = os.environ.get("EPL_FOTMOB_LEAGUE_ID", "47")
FOTMOB_LEAGUE_NAMES = {"premier league", "premierleague", "epl"}

# Season → (start, end) sweep window. Wide enough to catch pre-season openers and any
# rescheduled final-round games; extra empty days just cost a cheap HTTP request.
SEASON_WINDOWS: dict[str, tuple[str, str]] = {
    "2025-26": ("2025-08-01", "2026-06-15"),
    "2026-27": ("2026-08-01", "2027-06-15"),
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _fetch_day(day: date, retries: int = 3) -> str | None:
    url = f"https://api.fotmob.com/matches?date={day:%Y%m%d}"
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:
            if attempt == retries:
                print(f"  ! {day:%Y-%m-%d} failed after {retries} tries: {exc}")
                return None
            time.sleep(1.5 * attempt)
    return None


def _parse_utc(time_str: str) -> str:
    """FotMob 'DD.MM.YYYY HH:MM' -> ISO8601 UTC, or '' if unparseable."""
    try:
        dt = datetime.strptime(time_str, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return ""


def _is_epl(league) -> bool:
    lid = str(league.get("id", ""))
    name = league.get("name", "").strip().lower()
    if lid == str(FOTMOB_LEAGUE_ID):
        return True
    # id can drift between seasons; fall back to the exact name (excludes the Championship).
    return name in FOTMOB_LEAGUE_NAMES


def build_schedule(season: str, start: str | None = None, end: str | None = None,
                   verbose: bool = True) -> list[dict]:
    if season not in SEASON_WINDOWS and not (start and end):
        raise SystemExit(f"Unknown season {season!r}; pass --start/--end or use one of "
                         f"{sorted(SEASON_WINDOWS)}")
    win_start, win_end = SEASON_WINDOWS.get(season, ("", ""))
    s = datetime.strptime(start or win_start, "%Y-%m-%d").date()
    e = datetime.strptime(end or win_end, "%Y-%m-%d").date()

    by_id: dict[int, dict] = {}
    days = list(_daterange(s, e))
    if verbose:
        print(f"Sweeping {len(days)} days ({s} → {e}) for the Premier League (league {FOTMOB_LEAGUE_ID}) …")

    for i, day in enumerate(days, 1):
        xml = _fetch_day(day)
        if verbose and (i % 25 == 0 or i == len(days)):
            print(f"  … {i}/{len(days)} days, {len(by_id)} matches so far")
        if not xml:
            continue
        try:
            root = ET.fromstring(xml)
        except Exception:
            continue
        for league in root.iter("league"):
            if not _is_epl(league):
                continue
            for m in league.iter("match"):
                mid = m.get("id")
                if not mid:
                    continue
                try:
                    mid_i = int(mid)
                except ValueError:
                    continue
                status = m.get("Status", "N")
                hs, as_ = m.get("hScore"), m.get("aScore")
                finished = status in ("F", "FT", "AET", "PEN", "FT_PEN")
                try:
                    matchday = int(m.get("stage")) if m.get("stage") else None
                except ValueError:
                    matchday = None
                rec = {
                    "fotmob_id": mid_i,
                    "matchday": matchday,
                    "date": _parse_utc(m.get("time", ""))[:10] or None,
                    "kickoff_utc": _parse_utc(m.get("time", "")),
                    "home": m.get("hTeam", ""),
                    "away": m.get("aTeam", ""),
                    "home_id": m.get("hId"),
                    "away_id": m.get("aId"),
                    "home_score": int(hs) if finished and hs not in (None, "") else None,
                    "away_score": int(as_) if finished and as_ not in (None, "") else None,
                    "status": status,
                    "finished": finished,
                }
                # Prefer a finished record over a not-started duplicate of the same id.
                prev = by_id.get(mid_i)
                if prev is None or (rec["finished"] and not prev["finished"]):
                    by_id[mid_i] = rec

    matches = sorted(by_id.values(), key=lambda r: (r["matchday"] or 99,
                                                    r["kickoff_utc"] or "", r["fotmob_id"]))
    return matches


def _summarise(matches: list[dict]) -> None:
    finished = [m for m in matches if m["finished"]]
    teams: dict[str, int] = {}
    for m in finished:
        teams[m["home"]] = teams.get(m["home"], 0) + 1
        teams[m["away"]] = teams.get(m["away"], 0) + 1
    mds = sorted({m["matchday"] for m in matches if m["matchday"]})
    print("\n── Summary ─────────────────────────────────────────")
    print(f"  total matches : {len(matches)}")
    print(f"  finished      : {len(finished)}")
    print(f"  teams         : {len(teams)}")
    print(f"  matchdays     : {len(mds)} ({min(mds) if mds else '-'}–{max(mds) if mds else '-'})")
    if teams:
        gp = sorted(teams.items(), key=lambda kv: -kv[1])
        print(f"  games played  : max {gp[0][1]} ({gp[0][0]}), min {gp[-1][1]} ({gp[-1][0]})")
        off = [t for t, n in teams.items() if n != 38]
        if off:
            print(f"  ⚠ teams not on 38 games: {', '.join(sorted(off))}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a Premier League season schedule from FotMob.")
    ap.add_argument("--season", default="2025-26", help="e.g. 2025-26 or 2026-27")
    ap.add_argument("--start", help="override sweep start YYYY-MM-DD")
    ap.add_argument("--end", help="override sweep end YYYY-MM-DD")
    ap.add_argument("--out", help="output path (default schedules/SCHEDULE_<season>.json)")
    args = ap.parse_args()

    matches = build_schedule(args.season, args.start, args.end)
    _summarise(matches)

    SCHED_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else SCHED_DIR / f"SCHEDULE_{args.season}.json"
    payload = {
        "season": args.season,
        "competition": "Premier League",
        "fotmob_league_id": FOTMOB_LEAGUE_ID,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "matches": matches,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(matches)} matches → {out}")


if __name__ == "__main__":
    main()
