#!/usr/bin/env python3
"""Build per-team, per-SEASON player-event files for the Player Lab (ported from the
BCN dashboard, adapted to the whole league).

The Player Lab's stat cards / radar / head-to-head bars all read season aggregates
that already live in players.js. Only the ACTION MAPS (shots, take-ons, passes,
progressive passes) need per-player event locations. Those would be huge for all
600 players at once, so — like the match pages load matches_detail/<id>.js on
demand — we write ONE file per team and season (player_lab/<season>/<slug>.js)
that the Player Lab fetches when that team is picked.

Why per season: the first version keyed files by team only, so a player's maps
summed every scraped season (Raphinha "327 shots" in a 3-match season). Each
matches_detail file carries no season field; the match id → season mapping comes
from data.js (build_data.py), so run that first.

Each file:  window.LL_PLAYERLAB[<season>][<Team>] = { "<player>": {shots, dribbles, passes} }
Event arrays are compact and ordered to match app.js `plGraph`:
  shots    [x, y, gy, xg, goal, ontarget, min, opp]
  dribbles [x, y, -1, -1, ok, min, opp]        (WhoScored take-ons carry no end point)
  passes   [x, y, ex, ey, ok, prog, min, opp]  (progressive map = passes with prog=1)
Coords are raw WhoScored 0-100 (same as the match centre). No tackles map: the
league matches_detail doesn't carry tackle events.
"""
import glob, json, os, re, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DETAIL_DIR = os.path.join(HERE, "matches_detail")
OUT_DIR = os.path.join(HERE, "player_lab")
DATA_JS = os.path.join(HERE, "data.js")


def slug(team):
    return re.sub(r"[^A-Za-z0-9]+", "_", team).strip("_")


def _read(path):
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", open(path, encoding="utf-8").read(), re.S)
    return json.loads(m.group(1)) if m else None


def _season_of_id():
    """match id (as string) -> season key, from data.js."""
    d = _read(DATA_JS) if os.path.exists(DATA_JS) else None
    out = {}
    for key, s in ((d or {}).get("seasons") or {}).items():
        for m in s.get("matches") or []:
            if m.get("id") is not None:
                out[str(m["id"])] = key
    return out


def main():
    season_of = _season_of_id()
    if not season_of:
        raise SystemExit("build_player_lab: data.js missing or empty — run build_data.py first")

    # season -> team -> player -> {"shots":[], "dribbles":[], "passes":[]}
    seasons = {}
    skipped = 0
    for f in sorted(glob.glob(os.path.join(DETAIL_DIR, "*.js"))):
        base = os.path.basename(f)
        if base.startswith("_"):
            continue
        mid = base[:-3]
        season = season_of.get(mid)
        if not season:
            skipped += 1          # e.g. a test file or a match not in any schedule
            continue
        d = _read(f)
        if not d:
            continue
        teams = seasons.setdefault(season, {})
        tn = {"home": d["home"]["name"], "away": d["away"]["name"]}
        opp = {"home": d["away"]["name"], "away": d["home"]["name"]}

        def rec(team, player):
            t = teams.setdefault(team, {})
            return t.setdefault(player, {"shots": [], "dribbles": [], "passes": []})

        for s in d.get("shots", []):
            p = s.get("player")
            side = s.get("team")
            if not p or side not in tn:
                continue
            gy = s.get("gy")
            rec(tn[side], p)["shots"].append([
                round(s.get("x", 0) or 0, 1), round(s.get("y", 0) or 0, 1),
                round(gy if gy is not None else 50.0, 1),
                round(float(s.get("xg", 0) or 0), 3),
                1 if s.get("goal") else 0, 1 if s.get("onTarget") else 0,
                int(s.get("min", 0) or 0), opp[side],
            ])
        for dr in d.get("dribbles", []):
            p = dr.get("player")
            side = dr.get("team")
            if not p or side not in tn:
                continue
            rec(tn[side], p)["dribbles"].append([
                round(dr.get("x", 0) or 0, 1), round(dr.get("y", 0) or 0, 1),
                -1, -1, 1 if dr.get("ok") else 0, int(dr.get("min", 0) or 0), opp[side],
            ])
        for pa in d.get("passes", []):
            p = pa.get("player")
            side = pa.get("team")
            if not p or side not in tn:
                continue
            rec(tn[side], p)["passes"].append([
                round(pa.get("x", 0) or 0, 1), round(pa.get("y", 0) or 0, 1),
                round(pa.get("ex", 0) or 0, 1), round(pa.get("ey", 0) or 0, 1),
                1 if pa.get("ok") else 0, 1 if pa.get("prog") else 0,
                int(pa.get("min", 0) or 0), opp[side],
            ])

    # Rebuild the output tree from scratch so stale team-only files never linger.
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    idx = {}
    total = 0
    for season, teams in sorted(seasons.items()):
        sdir = os.path.join(OUT_DIR, season)
        os.makedirs(sdir, exist_ok=True)
        for team, players in teams.items():
            players = {p: v for p, v in players.items()
                       if v["shots"] or v["dribbles"] or v["passes"]}
            if not players:
                continue
            path = os.path.join(sdir, slug(team) + ".js")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("window.LL_PLAYERLAB = window.LL_PLAYERLAB || {};\n")
                fh.write("window.LL_PLAYERLAB[" + json.dumps(season) + "] = window.LL_PLAYERLAB[" + json.dumps(season) + "] || {};\n")
                fh.write("window.LL_PLAYERLAB[" + json.dumps(season) + "][" + json.dumps(team, ensure_ascii=False) + "] = ")
                json.dump(players, fh, ensure_ascii=False, separators=(",", ":"))
                fh.write(";\n")
            idx.setdefault(season, {})[team] = {"slug": slug(team), "players": len(players)}
            total += len(players)

    with open(os.path.join(OUT_DIR, "_index.js"), "w", encoding="utf-8") as fh:
        fh.write("window.LL_PLAYERLAB_TEAMS = ")
        json.dump(idx, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    for season in sorted(idx):
        print(f"  {season}: {len(idx[season])} team files")
    print(f"wrote player_lab/<season>/<team>.js for {len(idx)} seasons ({total} player-seasons; {skipped} detail files skipped)")


if __name__ == "__main__":
    main()
