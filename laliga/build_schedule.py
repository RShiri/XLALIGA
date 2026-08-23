#!/usr/bin/env python3
"""
Build a La Liga season schedule (fixtures + results) from FotMob's token-free XML feed.

FotMob's ``api.fotmob.com/matches?date=YYYYMMDD`` endpoint returns, per day, every
league's matches with real team names, team ids, kick-off time, matchday (``stage``)
and — for finished games — the final score (``Status='F'``, ``hScore``/``aScore``).
No token, no browser required. This module sweeps every date in a season's window,
keeps only La Liga (FotMob league id 87, name "LaLiga" — NOT "LaLiga2" id 901075),
de-duplicates by match id and writes ``laliga/schedules/SCHEDULE_<season>.json``.

That JSON is the spine of the dashboard: the standings table, the results/fixtures
list and the matchday grouping are all derived from it. The rich per-match data
(xG, shot/pass/dribble maps, player stats) is layered on later by the browser
scrapers (see ``laliga/run_match.py`` / ``laliga/backfill.py``); this file needs
none of that.

Usage:
    py laliga/build_schedule.py --season 2026-27          # incremental: only what's new
    py laliga/build_schedule.py --season 2026-27 --full   # the whole season window
    py laliga/build_schedule.py --season 2025-26 --start 2025-08-01 --end 2026-06-15

By default the sweep is **incremental**: it starts a few days before the last finished match
already in ``SCHEDULE_<season>.json`` and runs to a fortnight past today, then merges into that
file. A mid-season refresh is then ~20 requests instead of 300+. Use ``--full`` when a season is
new (to pull its complete fixture list) or when you suspect the file has drifted.
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

# The Windows console here is a legacy codepage (cp1255); force UTF-8 so accented
# club names (Alavés, Leganés) and any glyphs print instead of crashing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
SCHED_DIR = _HERE / "schedules"

# FotMob league id for La Liga (override with LALIGA_FOTMOB_LEAGUE_ID). The XML feed
# tags the top flight as name "LaLiga" id 87; the second tier is "LaLiga2" id 901075,
# which we must exclude.
FOTMOB_LEAGUE_ID = os.environ.get("LALIGA_FOTMOB_LEAGUE_ID", "87")
FOTMOB_LEAGUE_NAMES = {"laliga", "laliga ea sports", "la liga"}

# Season → (start, end) sweep window. Wide enough to catch pre-season openers and any
# rescheduled final-round games; extra empty days just cost a cheap HTTP request.
SEASON_WINDOWS: dict[str, tuple[str, str]] = {
    "2022-23": ("2022-08-01", "2023-06-30"),
    "2023-24": ("2023-08-01", "2024-06-30"),
    "2024-25": ("2024-08-01", "2025-06-30"),
    "2025-26": ("2025-08-01", "2026-06-15"),
    "2026-27": ("2026-08-01", "2027-06-15"),
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# FotMob's team names drift across (esp. older) seasons — e.g. 2023-24 tags Athletic as
# both "Athletic Bilbao" and "Athletic Club", and Atlético with/without the accent. Left
# alone, one club splits into two half-rows in the standings and misses its crest/colour.
# Collapse the known variant spellings to the one canonical name team_colors.py / the
# crests use. Keyed on the *variant*; the canonical spellings are never keys, so re-running
# a clean season (24/25, 25/26) is a no-op.
TEAM_NAME_CANON: dict[str, str] = {
    "Athletic Bilbao": "Athletic Club",
    "Atlético Madrid": "Atletico Madrid",
    "Atletico de Madrid": "Atletico Madrid",
}


def _canon_team(name: str) -> str:
    return TEAM_NAME_CANON.get((name or "").strip(), (name or "").strip())


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


def _is_laliga(league) -> bool:
    lid = str(league.get("id", ""))
    name = league.get("name", "").strip().lower()
    if lid == str(FOTMOB_LEAGUE_ID):
        return True
    # id can drift between seasons; fall back to the exact name (excludes "LaLiga2").
    return name in FOTMOB_LEAGUE_NAMES


def _iso_from_any(raw: str) -> str:
    """FotMob time as either 'DD.MM.YYYY HH:MM' (XML) or ISO-8601 (JSON) -> ISO-8601 UTC."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    iso = _parse_utc(raw)
    if iso:
        return iso
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return ""


def _record(mid: int, matchday, kickoff: str, home: str, away: str,
            home_id, away_id, status: str, hs, as_) -> dict:
    finished = status in ("F", "FT", "AET", "PEN", "FT_PEN")
    return {
        "fotmob_id": mid,
        "matchday": matchday,
        "date": kickoff[:10] or None,
        "kickoff_utc": kickoff,
        "home": _canon_team(home),
        "away": _canon_team(away),
        "home_id": home_id,
        "away_id": away_id,
        "home_score": int(hs) if finished and hs not in (None, "") else None,
        "away_score": int(as_) if finished and as_ not in (None, "") else None,
        "status": status,
        "finished": finished,
    }


def _from_xml(body: str) -> "list[dict] | None":
    """Parse the XML feed. None means 'this body isn't XML' (vs. [] = no La Liga that day)."""
    try:
        root = ET.fromstring(body)
    except Exception:
        return None
    out = []
    for league in root.iter("league"):
        if not _is_laliga(league):
            continue
        for m in league.iter("match"):
            try:
                mid = int(m.get("id") or "")
            except ValueError:
                continue
            try:
                matchday = int(m.get("stage")) if m.get("stage") else None
            except ValueError:
                matchday = None
            out.append(_record(mid, matchday, _iso_from_any(m.get("time", "")),
                               m.get("hTeam", ""), m.get("aTeam", ""),
                               m.get("hId"), m.get("aId"),
                               m.get("Status", "N"), m.get("hScore"), m.get("aScore")))
    return out


def _from_json(body: str) -> "list[dict] | None":
    """Parse the JSON form of the same endpoint — FotMob has flipped format before, and a
    silent format change is indistinguishable from 'no matches' unless we try both."""
    try:
        data = json.loads(body)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    out = []
    for league in data.get("leagues") or []:
        lid = str(league.get("primaryId") or league.get("id") or "")
        name = str(league.get("name", "")).strip().lower()
        if lid != str(FOTMOB_LEAGUE_ID) and name not in FOTMOB_LEAGUE_NAMES:
            continue
        for m in league.get("matches") or []:
            try:
                mid = int(m.get("id"))
            except (TypeError, ValueError):
                continue
            st = m.get("status") or {}
            rnd = m.get("round", m.get("roundName"))
            try:
                matchday = int(rnd) if str(rnd).strip().isdigit() else None
            except (TypeError, ValueError):
                matchday = None
            hs = as_ = None
            score = str(st.get("scoreStr") or "")
            if "-" in score:
                left, _, right = score.partition("-")
                hs, as_ = left.strip(), right.strip()
            home, away = m.get("home") or {}, m.get("away") or {}
            status = "F" if st.get("finished") else ("L" if st.get("started") else "N")
            out.append(_record(mid, matchday, _iso_from_any(st.get("utcTime", "")),
                               home.get("name") or home.get("longName") or "",
                               away.get("name") or away.get("longName") or "",
                               home.get("id"), away.get("id"), status, hs, as_))
    return out


def sweep_window(season: str, existing: "list[dict] | None", start: str | None,
                 end: str | None, full: bool, days_ahead: int) -> "tuple[date, date]":
    """Which days to actually ask FotMob about.

    A full season is 300+ requests, which is silly for "what happened since last time".
    With a schedule already on disk we sweep from a few days before its last finished
    match to a fortnight out, and merge; ``--full`` forces the whole season window.
    """
    win_start, win_end = SEASON_WINDOWS.get(season, ("", ""))
    s = datetime.strptime(start or win_start, "%Y-%m-%d").date()
    e = datetime.strptime(end or win_end, "%Y-%m-%d").date()
    if start or end or full:
        return s, e
    today = date.today()
    played = [m.get("date") for m in (existing or []) if m.get("finished") and m.get("date")]
    if played:
        s = max(s, datetime.strptime(max(played), "%Y-%m-%d").date() - timedelta(days=3))
    e = min(e, today + timedelta(days=days_ahead))
    if e < s:
        e = s
    return s, e


def merge_matches(old: "list[dict]", new: "list[dict]") -> "list[dict]":
    """Fold a partial sweep into what's already on disk — newer record wins per match id."""
    by_id = {m["fotmob_id"]: m for m in (old or [])}
    for m in new:
        prev = by_id.get(m["fotmob_id"])
        # Never let a "not started" re-read overwrite a result we already have.
        if prev and prev.get("finished") and not m.get("finished"):
            continue
        by_id[m["fotmob_id"]] = m
    return sorted(by_id.values(), key=lambda r: (r["matchday"] or 99,
                                                 r["kickoff_utc"] or "", r["fotmob_id"]))


def build_schedule(season: str, start: str | None = None, end: str | None = None,
                   verbose: bool = True, existing: "list[dict] | None" = None,
                   full: bool = False, days_ahead: int = 14) -> list[dict]:
    if season not in SEASON_WINDOWS and not (start and end):
        raise SystemExit(f"Unknown season {season!r}; pass --start/--end or use one of "
                         f"{sorted(SEASON_WINDOWS)}")
    s, e = sweep_window(season, existing, start, end, full, days_ahead)

    by_id: dict[int, dict] = {}
    days = list(_daterange(s, e))
    fetched = unreadable = with_league = 0
    sample = ""
    if verbose:
        print(f"Sweeping {len(days)} days ({s} → {e}) for La Liga (league {FOTMOB_LEAGUE_ID}) …")

    for i, day in enumerate(days, 1):
        body = _fetch_day(day)
        if verbose and (i % 25 == 0 or i == len(days)):
            print(f"  … {i}/{len(days)} days, {len(by_id)} matches so far")
        if not body:
            continue
        fetched += 1
        recs = _from_xml(body)
        if recs is None:
            recs = _from_json(body)
        if recs is None:                       # neither XML nor JSON — the feed changed
            unreadable += 1
            sample = sample or body[:200].replace("\n", " ")
            continue
        if recs:
            with_league += 1
        for rec in recs:
            prev = by_id.get(rec["fotmob_id"])
            if prev is None or (rec["finished"] and not prev["finished"]):
                by_id[rec["fotmob_id"]] = rec

    if verbose and not by_id:
        print("\n⚠ No La Liga matches found in that window.")
        if unreadable:
            print(f"  {unreadable}/{fetched} responses were neither XML nor JSON — FotMob's feed "
                  f"format has changed. First response started with:\n    {sample}")
        elif not fetched:
            print("  Every request failed — no network, or FotMob is blocking this machine.")
        else:
            print(f"  {fetched} days answered fine but none listed league {FOTMOB_LEAGUE_ID} "
                  f"({'/'.join(sorted(FOTMOB_LEAGUE_NAMES))}). Either the fixtures aren't published "
                  f"yet, or the league id changed (set LALIGA_FOTMOB_LEAGUE_ID).")
    elif verbose:
        print(f"  ({with_league} of {fetched} answered days had La Liga matches)")

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
        if off and len(finished) >= 380:
            print(f"  ⚠ teams not on 38 games: {', '.join(sorted(off))}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a La Liga season schedule from FotMob.")
    ap.add_argument("--season", default="2025-26", help="e.g. 2025-26 or 2026-27")
    ap.add_argument("--start", help="override sweep start YYYY-MM-DD")
    ap.add_argument("--end", help="override sweep end YYYY-MM-DD")
    ap.add_argument("--full", action="store_true",
                    help="sweep the whole season window instead of just what's new "
                         "(needed once per season to pull the complete fixture list)")
    ap.add_argument("--days-ahead", type=int, default=14,
                    help="how far past today to look for newly-listed fixtures (default 14)")
    ap.add_argument("--out", help="output path (default schedules/SCHEDULE_<season>.json)")
    args = ap.parse_args()

    SCHED_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else SCHED_DIR / f"SCHEDULE_{args.season}.json"

    # Load what we already have: it decides where the sweep starts, and a partial sweep
    # must never drop the rest of the season.
    existing: list[dict] = []
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8")).get("matches", []) or []
        except Exception as exc:
            print(f"! Could not read {out.name} ({exc}); treating it as empty.")
    if existing:
        print(f"{out.name}: {len(existing)} match(es) already known "
              f"({sum(1 for m in existing if m.get('finished'))} finished).")

    found = build_schedule(args.season, args.start, args.end, existing=existing,
                           full=args.full, days_ahead=args.days_ahead)
    matches = merge_matches(existing, found)
    added = len(matches) - len(existing)
    newly_finished = (sum(1 for m in matches if m.get("finished"))
                      - sum(1 for m in existing if m.get("finished")))
    _summarise(matches)

    payload = {
        "season": args.season,
        "competition": "LaLiga",
        "fotmob_league_id": FOTMOB_LEAGUE_ID,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "matches": matches,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(matches)} matches → {out}"
          f"  (+{added} new fixture(s), +{newly_finished} new result(s) this run)")


if __name__ == "__main__":
    main()
