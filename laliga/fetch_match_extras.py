#!/usr/bin/env python3
"""
Per-match stats that only FotMob's match feed carries, for the Match Centre's "More match stats" block.

Writes <package>/team_stats/MATCH_EXTRAS_<season>.json, one record per finished match, as
[home, away] pairs. build_match_details.py attaches the record to matches_detail/<id>.js.
Rows FotMob has no data for (tracking stats before 2025-26, for example) are simply left out.

Incremental: a match already in the file is not fetched again, so a run after a matchday costs
one request per new match.

Usage:
    py <package>/fetch_match_extras.py                    # every season on disk, newest first
    py <package>/fetch_match_extras.py --season 2026-27
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PKG_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PKG_DIR))
from build_schedule import _UA  # noqa: E402

DETAILS_URL = "https://www.fotmob.com/api/data/matchDetails?matchId={id}"
SCHED_DIR = PKG_DIR / "schedules"
OUT_DIR = PKG_DIR / "team_stats"
PAUSE_SECONDS = 0.5
TEAM_STATS = {
    "corners": "corners",
    "shots_inside_box": "shots_inside",
    "shots_outside_box": "shots_outside",
    "shots_woodwork": "woodwork",
    "touches_opp_box": "touches_box",
    "physical_metrics_number_of_sprints": "sprints",
}
PLAYER_TOTALS = {"passes_into_final_third": "final_third", "line_breaking_passes": "line_breaking"}


def _get(url: str):
    """Parsed JSON, or None. Asks for gzip only, so the optional brotli package isn't needed."""
    headers = {"User-Agent": _UA, "Accept": "application/json", "Accept-Encoding": "gzip",
               "Referer": "https://www.fotmob.com/"}
    if os.environ.get("FOTMOB_XMAS_TOKEN"):
        headers["x-mas"] = os.environ["FOTMOB_XMAS_TOKEN"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            body = r.read()
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8", "replace"))
    except (OSError, ValueError) as exc:
        print(f"  ! {url}: {exc}")
        return None


def _number(value):
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def _whole(value):
    return int(value) if value is not None and float(value).is_integer() else value


def parse(details: dict) -> "dict | None":
    content = details.get("content") or {}
    general = details.get("general") or {}
    home_id = (general.get("homeTeam") or {}).get("id")
    away_id = (general.get("awayTeam") or {}).get("id")
    groups = (((content.get("stats") or {}).get("Periods") or {}).get("All") or {}).get("stats") or []
    if home_id is None or not groups:
        return None

    out = {}
    for group in groups:
        for stat in group.get("stats") or []:
            name = TEAM_STATS.get(stat.get("key"))
            pair = stat.get("stats") or []
            if name and name not in out and len(pair) == 2:
                h, a = _number(pair[0]), _number(pair[1])
                if h is not None or a is not None:
                    out[name] = [_whole(h), _whole(a)]

    totals = {name: [None, None] for name in PLAYER_TOTALS.values()}
    fastest = [None, None]
    for player in (content.get("playerStats") or {}).values():
        team = player.get("teamId")
        side = 0 if team == home_id else 1 if team == away_id else None
        if side is None:
            continue
        seen = set()  # "Top stats" repeats some entries from the detailed groups
        for group in player.get("stats") or []:
            for item in (group.get("stats") or {}).values():
                key = item.get("key")
                if key in seen:
                    continue
                seen.add(key)
                value = _number((item.get("stat") or {}).get("value"))
                if value is None:
                    continue
                if key in PLAYER_TOTALS:
                    bucket = totals[PLAYER_TOTALS[key]]
                    bucket[side] = (bucket[side] or 0) + value
                elif key == "physical_metrics_topspeed" and value > 0:
                    if fastest[side] is None or value > fastest[side][0]:
                        fastest[side] = (value, player.get("name") or "")
    for name, pair in totals.items():
        if pair != [None, None]:
            out[name] = [_whole(pair[0]), _whole(pair[1])]
    if fastest != [None, None]:
        out["top_speed"] = [round(f[0], 1) if f else None for f in fastest]
        out["top_speed_players"] = [f[1] if f else None for f in fastest]
    return out or None


def run_season(season: str) -> int:
    schedule = json.loads((SCHED_DIR / f"SCHEDULE_{season}.json").read_text(encoding="utf-8"))
    ids = [str(m["fotmob_id"]) for m in schedule.get("matches", [])
           if m.get("finished") and str(m.get("fotmob_id", "")).isdigit()]
    # A schedule can lag behind the scrapes, so anything already scraped counts as finished too.
    ids += [p.stem for p in (PKG_DIR / "matches" / season).glob("*.json") if p.stem.isdigit() and p.stem not in set(ids)]
    out_file = OUT_DIR / f"MATCH_EXTRAS_{season}.json"
    records = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else {}
    todo = [i for i in ids if i not in records]
    print(f"{season}: {len(ids)} finished, {len(todo)} to fetch")

    def save():
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                            encoding="utf-8")

    failures = 0
    for n, match_id in enumerate(todo, 1):
        details = _get(DETAILS_URL.format(id=match_id))
        record = parse(details) if isinstance(details, dict) else None
        if record:
            records[match_id] = record
            failures = 0
        else:
            failures += 1
            if failures >= 15:
                save()
                print(f"{season}: stopping after {failures} misses in a row, {len(records)} saved")
                return 1
        if n % 25 == 0:
            save()
        time.sleep(PAUSE_SECONDS)
    save()
    print(f"{season}: {len(records)} of {len(ids)} matches stored")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--season")
    args = ap.parse_args()
    seasons = [args.season] if args.season else sorted(
        (p.stem.replace("SCHEDULE_", "") for p in SCHED_DIR.glob("SCHEDULE_*.json")), reverse=True)
    return max(run_season(s) for s in seasons)


if __name__ == "__main__":
    raise SystemExit(main())
