#!/usr/bin/env python3
"""Build per-team player-event files for the Player Lab (ported from the BCN
dashboard, adapted to the whole league).

The Player Lab's stat cards / radar / head-to-head bars all read season aggregates
that already live in players.js. Only the ACTION MAPS (shots, take-ons, passes,
progressive passes) need per-player event locations. Those would be huge for all
600 players at once, so — like the match pages load matches_detail/<id>.js on
demand — we write ONE file per team (player_lab/<slug>.js) that the Player Lab
fetches when that team is picked.

Each file:  window.LL_PLAYERLAB[<Team>] = { "<season>": { "<player>": {shots, dribbles, passes} } }
Events are bucketed by SEASON (derived from each match's date) so the maps stay
season-scoped — matching the season-keyed stat cards / bars / radar in players.js.
Event arrays are compact and ordered to match app.js `playerGraph`:
  shots    [x, y, gy, xg, goal, ontarget, min, opp]
  dribbles [x, y, -1, -1, ok, min, opp]        (WhoScored take-ons carry no end point)
  passes   [x, y, ex, ey, ok, prog, min, opp]  (progressive map = passes with prog=1)
Coords are raw WhoScored 0-100 (same as the match centre). No tackles map: the
league matches_detail doesn't carry tackle events.
"""
import glob, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
DETAIL_DIR = os.path.join(HERE, "matches_detail")
OUT_DIR = os.path.join(HERE, "player_lab")


def slug(team):
    return re.sub(r"[^A-Za-z0-9]+", "_", team).strip("_")


def season_of(date):
    """La Liga season label for an ISO match date ("YYYY-MM-DD"). A season starts
    in July: start year = year if month>=7 else year-1; label "YYYY-YY"."""
    if not date or len(date) < 7:
        return None
    y, mo = int(date[0:4]), int(date[5:7])
    start = y if mo >= 7 else y - 1
    return "%d-%02d" % (start, (start + 1) % 100)


def _read(path):
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", open(path, encoding="utf-8").read(), re.S)
    return json.loads(m.group(1)) if m else None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    teams = {}   # team -> season -> {player -> {"shots":[], "dribbles":[], "passes":[]}}

    for f in sorted(glob.glob(os.path.join(DETAIL_DIR, "*.js"))):
        if os.path.basename(f).startswith("_"):
            continue
        d = _read(f)
        if not d:
            continue
        season = season_of(d.get("date"))
        if not season:
            continue
        tn = {"home": d["home"]["name"], "away": d["away"]["name"]}
        opp = {"home": d["away"]["name"], "away": d["home"]["name"]}

        def rec(team, player):
            t = teams.setdefault(team, {}).setdefault(season, {})
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

    # drop empty players; write one season-nested file per team
    idx = {}
    for team, seasons in teams.items():
        clean = {}
        for season, players in seasons.items():
            players = {p: v for p, v in players.items()
                       if v["shots"] or v["dribbles"] or v["passes"]}
            if players:
                clean[season] = players
        if not clean:
            continue
        path = os.path.join(OUT_DIR, slug(team) + ".js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("window.LL_PLAYERLAB = window.LL_PLAYERLAB || {};\n")
            fh.write("window.LL_PLAYERLAB[" + json.dumps(team, ensure_ascii=False) + "] = ")
            json.dump(clean, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write(";\n")
        distinct = set()
        for players in clean.values():
            distinct.update(players)
        idx[team] = {"slug": slug(team), "players": len(distinct), "seasons": sorted(clean)}

    with open(os.path.join(OUT_DIR, "_index.js"), "w", encoding="utf-8") as fh:
        fh.write("window.LL_PLAYERLAB_TEAMS = ")
        json.dump(idx, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    tot = sum(v["players"] for v in idx.values())
    nseasons = len({s for v in teams.values() for s in v})
    print(f"wrote {len(idx)} team files to {OUT_DIR}  ({tot} players across {nseasons} seasons)")


if __name__ == "__main__":
    main()
