#!/usr/bin/env python3
"""
Fetch FotMob's season team stat "Total distance per match" (km) for LaLiga.

Distance covered is tracking data, not event data — WhoScored/Opta events never carry it,
so the rich per-match scrape can't produce it. FotMob publishes it as a league-level
*team* stat (League → Stats → Teams → "Total distance per match"), one row per club with
the average km and the number of matches it covers. This script pulls that list in one or
two requests and writes ``laliga/team_stats/DISTANCE_<season>.json``; ``build_data.py`` then
attaches it to the season so the dashboard's Team Lab can show an "Avg km per game" table.

How the stat is located (FotMob has renamed things before, so nothing is hard-coded to one URL):
  1. ``www.fotmob.com/api/data/leagues?id=87&season=2026/2027`` carries a ``stats`` block whose
     team entries each point at a ``data.fotmob.com/stats/...json`` list — take the one whose
     name/header mentions "distance".
  2. Failing that, build candidate URLs from the season's stats tournament id
     (``data.fotmob.com/stats/87/season/<id>/<name>.json``) for a few likely stat names.
If neither yields a list, nothing is written (an older file on disk is left alone) and the
script exits non-zero. ``--dump`` prints every team stat the league payload advertises, so a
future rename is a one-line fix to ``_NAME_HINTS``/``_CANDIDATE_STATS``.

Usage:
    py laliga/fetch_team_stats.py --season 2026-27
    py laliga/fetch_team_stats.py --season 2026-27 --dump
"""

from __future__ import annotations

import os
import re
import sys
import json
import gzip
import zlib
import argparse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_schedule import (FOTMOB_LEAGUE_ID, FOTMOB_SEASON_URL, _UA, _fetch_json,  # noqa: E402
                            _season_param)

OUT_DIR = Path(__file__).resolve().parent / "team_stats"

_NAME_HINTS = ("distance", "phys_tdc")   # FotMob's id for "total distance covered" is phys_tdc*
_CANDIDATE_STATS = ("distance_covered_team", "total_distance_team", "distance_team",
                    "distance_covered_per_match_team", "total_distance_per_match_team")


def _walk(node, depth=0):
    """Yield every dict in a JSON tree."""
    if depth > 12:
        return
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, depth + 1)


def _stat_links(league: dict) -> "list[tuple[str, str]]":
    """(label, url) for every stats list the league payload links to."""
    out = []
    for d in _walk(league.get("stats") or league):
        url = next((v for v in d.values() if isinstance(v, str) and "data.fotmob.com/stats" in v), None)
        if not url:
            continue
        label = " / ".join(str(d[k]) for k in ("header", "title", "name", "localizedTitleId")
                           if isinstance(d.get(k), str))
        out.append((label or url.rsplit("/", 1)[-1], url))
    return out


def _tournament_ids(league: dict, season: str) -> "list[str]":
    """Stats tournament ids for this season, from any link/field the payload carries."""
    ids = []
    want = _season_param(season)
    for d in _walk(league):
        name = str(d.get("Name") or d.get("name") or "")
        tid = d.get("TournamentId") or d.get("tournamentId")
        if tid and (not name or want in name):
            ids.append(str(tid))
        for v in d.values():
            if isinstance(v, str):
                m = re.search(r"data\.fotmob\.com/stats/\d+/season/(\d+)/", v)
                if m:
                    ids.append(m.group(1))
    return list(dict.fromkeys(ids))


def _get(url: str):
    """GET a stats file and return parsed JSON, or None. Unlike build_schedule._fetch_json this
    copes with a gzip/deflate/brotli body and, when the body still isn't JSON, prints what came
    back (status, type, first bytes) so a format change is diagnosable from one run."""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br", "Referer": "https://www.fotmob.com/",
        **({"x-mas": os.environ["FOTMOB_XMAS_TOKEN"]} if os.environ.get("FOTMOB_XMAS_TOKEN") else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body, enc, ctype, status = r.read(), (r.headers.get("Content-Encoding") or "").lower(), \
                r.headers.get("Content-Type", ""), r.status
    except Exception as exc:
        print(f"  ! {url}: {exc}")
        return None
    try:
        if enc == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        elif enc == "deflate":
            body = zlib.decompress(body)
        elif enc == "br":
            import brotli  # pip install brotli
            body = brotli.decompress(body)
    except Exception as exc:
        print(f"  ! {url}: could not decode {enc or 'body'} ({exc})")
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        print(f"  ! {url}: HTTP {status}, {ctype or 'no content-type'}, {len(body)} bytes, "
              f"not JSON — starts {body[:120]!r}")
        return None


_NAME_KEYS = ("ParticipantName", "participantName", "TeamName", "teamName", "name")
_ID_KEYS = ("TeamId", "teamId", "ParticipantId", "ParticiantId", "participantId", "id")
_VAL_KEYS = ("StatValue", "statValue", "value")
_MP_KEYS = ("MatchesPlayed", "matchesPlayed", "matches")


def _row(d: dict):
    name = next((d[k] for k in _NAME_KEYS if isinstance(d.get(k), str) and d[k]), None)
    val = None
    for k in _VAL_KEYS:
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("value", v.get("Value"))
        val = _num(v)
        if val is not None:
            break
    if not name or val is None:
        return None
    mp = next((d[k] for k in _MP_KEYS if isinstance(d.get(k), (int, float))), None)
    tid = next((d[k] for k in _ID_KEYS if d.get(k) not in (None, "")), "")
    return {"team": name, "team_id": str(tid), "value": val, "matches": int(mp) if mp else None}


def _parse_any(payload) -> "tuple[str, list[dict]]":
    """(title, rows) from any FotMob stat payload: the classic TopLists/StatList layout first,
    else the first list in the tree whose items look like {team name, numeric value} rows."""
    title, rows = _parse_list(payload) if isinstance(payload, dict) else ("", [])
    if rows:
        return title, rows
    for d in _walk(payload):
        for k, v in d.items():
            if isinstance(v, list) and len(v) >= 10 and all(isinstance(x, dict) for x in v):
                rs = [r for r in (_row(x) for x in v) if r]
                if len(rs) >= 10:
                    t = next((str(d[t]) for t in ("title", "Title", "name", "header") if isinstance(d.get(t), str)), "")
                    return t, rs
    return "", []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_list(payload: dict) -> "tuple[str, list[dict]]":
    """(title, rows) from a data.fotmob.com stats list. Rows: team, team_id, value, matches."""
    lists = payload.get("TopLists") or payload.get("topLists") or []
    if not lists:
        return "", []
    top = lists[0]
    title = str(top.get("Title") or top.get("StatName") or top.get("title") or "")
    rows = []
    for r in top.get("StatList") or top.get("statList") or []:
        team = r.get("ParticipantName") or r.get("TeamName") or r.get("participantName")
        val = _num(r.get("StatValue") if "StatValue" in r else r.get("statValue"))
        if not team or val is None:
            continue
        mp = r.get("MatchesPlayed") or r.get("matchesPlayed")
        rows.append({"team": team,
                     "team_id": str(r.get("TeamId") or r.get("ParticiantId") or r.get("ParticipantId") or ""),
                     "value": val, "matches": int(mp) if mp else None})
    return title, rows


def _to_km_per_match(rows: "list[dict]") -> None:
    """Normalise each row to km per match in place. FotMob shows km per match (≈95–125);
    guard against a feed that switches to metres or to a season total."""
    for r in rows:
        v, mp = r["value"], r.get("matches")
        if v > 1000:                       # metres per match (or a season total in metres)
            v = v / 1000
        if v > 200 and mp:                 # season total in km
            v = v / mp
        r["km"] = round(v, 2)


def fetch_distance(season: str, dump: bool = False) -> "dict | None":
    league = _fetch_json(FOTMOB_SEASON_URL.format(league=FOTMOB_LEAGUE_ID,
                                                  season=_season_param(season).replace("/", "%2F")))
    if not league:
        print("  ! FotMob league payload unavailable (network/firewall?).")
        return None
    links = _stat_links(league)
    if dump:
        print(f"{len(links)} stat lists advertised:")
        for label, url in links:
            print(f"  {label:<45} {url}")

    hits = [u for label, u in links if any(h in (label + u).lower() for h in _NAME_HINTS)]
    # Team lists first ("..._team.json"); per-90 lists are per player, never what we want.
    hits.sort(key=lambda u: ("_team" not in u, "per_90" in u))
    urls = [u for u in hits if "per_90" not in u]
    tids = _tournament_ids(league, season)
    names = [u.rsplit("/", 1)[-1][:-5] for u in urls if u.endswith(".json")] or list(_CANDIDATE_STATS)
    # FotMob's own stats page reads these through its site API rather than the data.fotmob.com
    # files; try that too for every stat name we know about.
    for tid in tids:
        for n in dict.fromkeys(names + ["phys_tdc_team"]):
            urls.append(f"https://www.fotmob.com/api/data/leagueseasondeepstats?id={FOTMOB_LEAGUE_ID}"
                        f"&season={tid}&type=teams&stat={n}")
        urls += [f"https://data.fotmob.com/stats/{FOTMOB_LEAGUE_ID}/season/{tid}/{s}.json"
                 for s in _CANDIDATE_STATS]

    for url in dict.fromkeys(urls):
        payload = _get(url)
        if not payload:
            continue
        title, rows = _parse_any(payload)
        if not rows:
            continue
        _to_km_per_match(rows)
        rows.sort(key=lambda r: -r["km"])
        median = rows[len(rows) // 2]["km"]
        if not 80 <= median <= 140:   # a team covers ~100-120 km a match; one player ~10
            print(f"  ! {url}: median {median} km doesn't look like team distance per match — skipped")
            continue
        print(f"  ✓ {title or 'distance'}: {len(rows)} teams from {url}")
        return {"season": season, "stat": title or "Total distance per match", "unit": "km/match",
                "source": url, "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "teams": [{"team": r["team"], "team_id": r["team_id"], "km": r["km"],
                           "matches": r["matches"]} for r in rows]}
    print("  ! No distance stat found — run with --dump to see what FotMob lists.")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="FotMob team distance-per-match → laliga/team_stats/.")
    ap.add_argument("--season", required=True, help="e.g. 2026-27")
    ap.add_argument("--dump", action="store_true", help="List every stat the league payload links to.")
    args = ap.parse_args()
    print(f"── FotMob team distance — {args.season} ──")
    data = fetch_distance(args.season, dump=args.dump)
    if not data:
        sys.exit(1)
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"DISTANCE_{args.season}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
