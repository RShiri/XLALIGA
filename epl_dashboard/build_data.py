#!/usr/bin/env python3
"""Build data.js for the Premier League dashboard — one payload per season, from the FotMob
schedule (spine: fixtures/results/standings) plus any rich per-match scrapes.

Unlike the WC2026 builder (whose primary source was the rich scraped match JSONs), a
league is driven by its **schedule**: ``epl/schedules/SCHEDULE_<season>.json`` already
carries every fixture with real scores + matchday (built token-free from FotMob by
``epl/build_schedule.py``). Rich per-match data (xG, shots, events) is *layered on*
when present in ``epl/matches/<season>/<fotmob_id>.json`` — the same files the browser
scrapers (``epl/run_match.py`` / ``epl/backfill.py``) produce. Matches with no rich
file still appear with their score/standings contribution; the xG lab simply fills in as
matches get deep-scraped.

Output: one self-contained ``data.js`` →
    window.LL_DATA = { "2025-26": {...}, "2026-27": {...} };
so the static site works by just opening index.html (no build step at view time).
"""
from __future__ import annotations

import os
import sys
import json
import glob
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xg_model import team_xg_from_events  # shared shot-extraction + xG (matches the PNGs)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHED_DIR = os.path.join(ROOT, "epl", "schedules")
MATCH_DIR = os.environ.get("EPL_MATCH_DIR") or os.path.join(ROOT, "epl", "matches")  # rich scrapes: <season>/<id>.json
PNG_DIRS = [os.path.join(ROOT, "epl_png"), os.path.join(ROOT, "epl", "output")]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.js")

FOTMOB_CREST = "https://images.fotmob.com/image_resources/logo/teamlogo/{id}.png"
CREST_DIR = os.path.join(ROOT, "team_logos", "epl")


def _crest(team, team_id):
    """Prefer a locally downloaded crest (self-contained site); fall back to the CDN."""
    local = os.path.join(CREST_DIR, f"{team}.png")
    if os.path.exists(local):
        return f"../team_logos/epl/{team}.png"
    return FOTMOB_CREST.format(id=team_id) if team_id else ""

# Canonical stat keys we surface (same set the WC dashboard used), mapped to how the
# scraper stores them in a rich match JSON's ``match_stats`` (nested {home,away} or flat).
_STAT_KEYS = {
    "xg": "xg", "shots": "shots", "sot": "shots_on_target",
    "possession": "possession", "passes": "passes_total",
    "pass_acc": "passes_accuracy", "big_chances": "big_chances_created",
    "big_missed": "big_chances_missed", "saves": "saves", "fouls": "fouls",
    "duels_won": "duels_won", "corners": "corners",
}


def _pair(ms, canonical):
    v = ms.get(canonical)
    if isinstance(v, dict):
        return [v.get("home"), v.get("away")]
    return [ms.get(canonical + "_home"), ms.get(canonical + "_away")]


def _stat_line(ms):
    line = {dst: _pair(ms, canon) for dst, canon in _STAT_KEYS.items()}
    if line["pass_acc"] == [None, None]:
        line["pass_acc"] = _pair(ms, "pass_accuracy")
    return line


def _find_png(season, fotmob_id):
    for d in PNG_DIRS:
        p = os.path.join(d, f"{fotmob_id}.png")
        if os.path.exists(p):
            return os.path.relpath(p, os.path.dirname(OUT)).replace("\\", "/")
    return None


def _load_rich(season, fotmob_id):
    """Return the rich scraped match JSON for a fixture, or None."""
    for cand in (os.path.join(MATCH_DIR, season, f"{fotmob_id}.json"),
                 os.path.join(MATCH_DIR, f"{fotmob_id}.json")):
        if os.path.exists(cand):
            try:
                return json.load(open(cand, encoding="utf-8"))
            except Exception:
                return None
    return None


def build_matches(season, schedule):
    """Merge schedule fixtures with any rich scrape → the dashboard match list + crest map."""
    matches, crests = [], {}
    for f in schedule["matches"]:
        fid = f["fotmob_id"]
        home, away = f["home"], f["away"]
        if home not in crests:
            crests[home] = _crest(home, f.get("home_id"))
        if away not in crests:
            crests[away] = _crest(away, f.get("away_id"))

        hs, as_ = f.get("home_score"), f.get("away_score")
        stats = {k: [None, None] for k in _STAT_KEYS}
        xg_home = xg_away = None
        xg_estimated = False
        has_events = False
        sources = []

        rich = _load_rich(season, fid)
        if rich:
            ms = rich.get("match_stats") or {}
            stats = _stat_line(ms)
            xg_home, xg_away = stats["xg"][0], stats["xg"][1]
            sources = rich.get("_sources", [])
            has_events = bool(rich.get("events"))
            # Rich file is authoritative for the score if the schedule lacked it.
            if hs is None:
                hs = rich.get("home", {}).get("score")
            if as_ is None:
                as_ = rich.get("away", {}).get("score")
            if (xg_home is None or xg_away is None) and rich.get("events"):
                ch, ca = team_xg_from_events(rich)
                if ch is not None:
                    xg_home, xg_away, xg_estimated = ch, ca, True
                    stats["xg"] = [xg_home, xg_away]

        played = hs is not None and as_ is not None
        has_stats = stats["xg"][0] is not None or stats["shots"][0] is not None
        matches.append({
            "id": str(fid),
            "fotmob_id": fid,
            "date": f.get("date") or "",
            "matchday": f.get("matchday"),
            "venue": (rich or {}).get("meta", {}).get("venue", "") if rich else "",
            "home": home, "away": away,
            "home_id": f.get("home_id"), "away_id": f.get("away_id"),
            "hs": hs, "as": as_,
            "played": played,
            "upcoming": not played,
            "kickoff": f.get("kickoff_utc") or "",
            "has_stats": bool(has_stats),
            "has_events": has_events,
            "xg_home": xg_home, "xg_away": xg_away,
            "xg_estimated": xg_estimated,
            "png": _find_png(season, fid),
            "stats": stats,
            "sources": sources,
        })
    return matches, crests


def compute_standings(matches):
    """Single 20-team league table from finished results, ranked Pts→GD→GF→name."""
    T = {}
    for m in matches:
        if not m["played"]:
            continue
        h, a, hs, as_ = m["home"], m["away"], m["hs"], m["as"]
        for t in (h, a):
            T.setdefault(t, dict(team=t, P=0, W=0, D=0, L=0, GF=0, GA=0, GD=0, Pts=0, form=[]))
        H, A = T[h], T[a]
        H["P"] += 1; A["P"] += 1
        H["GF"] += hs; H["GA"] += as_; A["GF"] += as_; A["GA"] += hs
        if hs > as_:
            H["W"] += 1; H["Pts"] += 3; A["L"] += 1; H["form"].append(["W", m]); A["form"].append(["L", m])
        elif hs < as_:
            A["W"] += 1; A["Pts"] += 3; H["L"] += 1; A["form"].append(["W", m]); H["form"].append(["L", m])
        else:
            H["D"] += 1; A["D"] += 1; H["Pts"] += 1; A["Pts"] += 1
            H["form"].append(["D", m]); A["form"].append(["D", m])
    # Make sure every team that has a fixture appears, even with 0 played.
    for m in matches:
        for t in (m["home"], m["away"]):
            T.setdefault(t, dict(team=t, P=0, W=0, D=0, L=0, GF=0, GA=0, GD=0, Pts=0, form=[]))
    rows = list(T.values())
    for r in rows:
        r["GD"] = r["GF"] - r["GA"]
        # last-5 form (most recent first), by kickoff order
        r["form"] = [fm[0] for fm in sorted(r["form"], key=lambda x: x[1].get("kickoff") or x[1]["date"])][-5:]
    rows.sort(key=lambda r: (-r["Pts"], -r["GD"], -r["GF"], r["team"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def build_xg_records(matches):
    recs = []
    for m in matches:
        if not m["played"] or m["xg_home"] is None or m["xg_away"] is None:
            continue
        recs.append(dict(team=m["home"], opp=m["away"], gf=m["hs"], ga=m["as"],
                         xgf=round(m["xg_home"], 2), xga=round(m["xg_away"], 2), home=True, date=m["date"]))
        recs.append(dict(team=m["away"], opp=m["home"], gf=m["as"], ga=m["hs"],
                         xgf=round(m["xg_away"], 2), xga=round(m["xg_home"], 2), home=False, date=m["date"]))
    return recs


def build_season(season):
    path = os.path.join(SCHED_DIR, f"SCHEDULE_{season}.json")
    if not os.path.exists(path):
        return None
    schedule = json.load(open(path, encoding="utf-8"))
    if not schedule.get("matches"):
        # Empty (e.g. 26/27 before fixtures release) — render a "not started" shell.
        return {"season": season, "status": "not_started", "counts": {},
                "standings": [], "matches": [], "xgRecords": [], "crests": {}, "teams": []}

    matches, crests = build_matches(season, schedule)
    standings = compute_standings(matches)
    xg_records = build_xg_records(matches)
    played = [m for m in matches if m["played"]]
    with_xg = [m for m in played if m["xg_home"] is not None]
    mds = [m["matchday"] for m in matches if m["matchday"]]
    cur_md = max((m["matchday"] for m in played if m["matchday"]), default=0)
    return {
        "season": season,
        "status": "in_progress" if played and len(played) < len(matches) else
                  ("finished" if played else "not_started"),
        "counts": {
            "total": len(matches), "played": len(played), "with_xg": len(with_xg),
            "teams": len(standings), "matchdays": max(mds) if mds else 0,
            "current_matchday": cur_md,
        },
        "standings": standings,
        "matches": matches,
        "xgRecords": xg_records,
        "crests": crests,
        "teams": sorted(r["team"] for r in standings),
    }


def main():
    seasons = {}
    for season in ("2025-26", "2026-27"):
        s = build_season(season)
        if s is not None:
            seasons[season] = s
    if not seasons:
        raise SystemExit("No schedules found in " + SCHED_DIR + " — run epl/build_schedule.py first.")

    payload = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "defaultSeason": "2025-26" if "2025-26" in seasons else sorted(seasons)[0],
        "seasons": seasons,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("// AUTO-GENERATED by build_data.py — do not edit by hand.\n")
        fh.write("window.LL_DATA = ")
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write(";\n")
    print(f"Wrote {OUT}")
    for season, s in seasons.items():
        c = s.get("counts", {})
        print(f"  {season}: {c.get('played',0)}/{c.get('total',0)} played, "
              f"{c.get('with_xg',0)} with xG, {c.get('teams',0)} teams, status={s['status']}")


if __name__ == "__main__":
    main()
