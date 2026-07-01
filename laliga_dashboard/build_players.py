#!/usr/bin/env python3
"""Aggregate per-player statistics across every played match into players.js.

Combines both data sources the pipeline scrapes: WhoScored player stat streams
(passes, shots, tackles, ratings, …) and the event feed (goals, assists, cards,
minutes). Writes window.WC_PLAYERS for the dashboard's Players tab, and exposes
aggregate()/per_match_rows() for the database exporter.
"""
import json
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from build_match_details import norm, _match_extras, _player_rating, is_match_file
from xg_model import ascii_name, SHOT_TYPES, shot_xg, is_shootout

MATCH_DIR = os.path.join(ROOT, "laliga", "matches")
OUT = os.path.join(HERE, "players.js")

# WhoScored per-minute stat dicts are incremental → sum the values.
SUM_STATS = {
    "shotsTotal": "shots", "shotsOnTarget": "sot", "passesTotal": "passes",
    "passesAccurate": "passAcc", "passesKey": "keyPasses", "touches": "touches",
    "tacklesTotal": "tackles", "interceptions": "interceptions", "aerialsWon": "aerials",
    "dribblesWon": "dribbles", "foulsCommited": "fouls", "clearances": "clearances",
    "dispossessed": "dispossessed", "totalSaves": "saves",
}


def _sum_stat(stats, key):
    d = stats.get(key) or {}
    try:
        return sum(float(v) for v in d.values())
    except (TypeError, ValueError):
        return 0.0


def _new_player(pid, name, team, pos):
    rec = dict(pid=pid, name=name, team=team, pos=pos,
               mp=0, starts=0, mins=0, g=0, a=0, yc=0, rc=0,
               rating_sum=0.0, rating_n=0, rating_best=0.0, xg=0.0)
    for v in SUM_STATS.values():
        rec[v] = 0.0
    return rec


def _player_shot_xg(match_data):
    """playerId -> summed shot xG for the match."""
    out = {}
    for ev in match_data.get("events", []):
        t = ev.get("type", {})
        if not isinstance(t, dict) or t.get("displayName") not in SHOT_TYPES:
            continue
        if is_shootout(ev):
            continue  # exclude penalty-shootout kicks from player xG
        pid = ev.get("playerId")
        if pid is None:
            continue
        xg, _ = shot_xg(ev)
        out[pid] = out.get(pid, 0.0) + xg
    return out


def _iter_played(match_dir=MATCH_DIR):
    for f in sorted(glob.glob(os.path.join(match_dir, "*.json"))):
        if not is_match_file(f):
            continue  # skip scraper cache files
        d = json.load(open(f, encoding="utf-8"))
        if d["home"].get("score") is None or d["away"].get("score") is None:
            continue
        if not any((p.get("stats") or {}).get("ratings")
                   for p in d["home"].get("players", []) + d["away"].get("players", [])):
            continue  # no per-player stats (FotMob-only games)
        yield os.path.basename(f)[:-5], d


def aggregate(match_dir=MATCH_DIR):
    players = {}
    for mid, d in _iter_played(match_dir):
        ex = _match_extras(d)
        shot_xg_map = _player_shot_xg(d)
        for side in ("home", "away"):
            team = norm(d[side].get("name", ""))
            for p in d[side].get("players", []):
                pid = p.get("playerId")
                stats = p.get("stats") or {}
                started = bool(p.get("isFirstEleven"))
                on_m = ex["on_min"].get(pid)
                came_on = on_m is not None
                if not (started or came_on):
                    continue  # unused bench
                rec = players.get(pid)
                if rec is None:
                    rec = players[pid] = _new_player(pid, ascii_name(p.get("name", "")), team, p.get("position", ""))
                rec["mp"] += 1
                if started:
                    rec["starts"] += 1
                    rec["mins"] += (ex["off_min"].get(pid) if ex["off_min"].get(pid) is not None else ex["end_min"])
                else:
                    rec["mins"] += max(0, ex["end_min"] - on_m)
                rec["g"] += ex["goals"].get(pid, 0)
                rec["a"] += ex["assists"].get(pid, 0)
                rec["yc"] += ex["yellow"].get(pid, 0)
                rec["rc"] += ex["red"].get(pid, 0)
                rec["xg"] += shot_xg_map.get(pid, 0.0)
                rt = _player_rating(p)
                if rt is not None:
                    rec["rating_sum"] += rt
                    rec["rating_n"] += 1
                    rec["rating_best"] = max(rec["rating_best"], rt)
                for src, dst in SUM_STATS.items():
                    rec[dst] += _sum_stat(stats, src)

    out = []
    for rec in players.values():
        r = dict(rec)
        r["ga"] = r["g"] + r["a"]
        r["rating"] = round(r["rating_sum"] / r["rating_n"], 2) if r["rating_n"] else None
        r["rating_best"] = round(r["rating_best"], 2) if r["rating_best"] else None
        r["pass_pct"] = round(100 * r["passAcc"] / r["passes"]) if r["passes"] else None
        r["xg"] = round(r["xg"], 2)
        r["xg_diff"] = round(r["g"] - r["xg"], 2)
        for v in list(SUM_STATS.values()) + ["mins"]:
            r[v] = int(round(r[v]))
        r.pop("rating_sum", None)
        r.pop("rating_n", None)
        out.append(r)
    out.sort(key=lambda r: (-r["ga"], -r["g"], -(r["rating"] or 0)))
    return out


def per_match_rows():
    """Yield one flat dict per player per match (for the database export)."""
    for mid, d in _iter_played():
        ex = _match_extras(d)
        shot_xg_map = _player_shot_xg(d)
        date = d.get("meta", {}).get("date", "") or mid[:10].replace("_", "-")
        for side in ("home", "away"):
            team = norm(d[side].get("name", ""))
            opp = norm(d["away" if side == "home" else "home"].get("name", ""))
            for p in d[side].get("players", []):
                pid = p.get("playerId")
                stats = p.get("stats") or {}
                started = bool(p.get("isFirstEleven"))
                on_m = ex["on_min"].get(pid)
                if not (started or on_m is not None):
                    continue
                mins = (ex["off_min"].get(pid) if ex["off_min"].get(pid) is not None else ex["end_min"]) \
                    if started else max(0, ex["end_min"] - on_m)
                row = dict(match_id=mid, date=date, team=team, opponent=opp,
                           player_id=pid, player=ascii_name(p.get("name", "")),
                           position=p.get("position", ""), started=int(started), minutes=int(mins),
                           goals=ex["goals"].get(pid, 0), assists=ex["assists"].get(pid, 0),
                           yellow=ex["yellow"].get(pid, 0), red=ex["red"].get(pid, 0),
                           rating=_player_rating(p), xg=round(shot_xg_map.get(pid, 0.0), 2))
                for src, dst in SUM_STATS.items():
                    row[dst] = int(round(_sum_stat(stats, src)))
                yield row


def main():
    # Per-season aggregation: matches/<season>/<id>.json → window.LL_PLAYERS[season].
    seasons = {}
    for sd in sorted(glob.glob(os.path.join(MATCH_DIR, "*"))):
        if os.path.isdir(sd):
            seasons[os.path.basename(sd)] = aggregate(sd)
    # Also fold any loose files saved directly under matches/ into the default season.
    loose = aggregate(MATCH_DIR)
    if loose:
        seasons.setdefault("2025-26", [])
        seasons["2025-26"] = loose + seasons["2025-26"]
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.LL_PLAYERS = ")
        json.dump(seasons, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    summary = ", ".join(f"{k}:{len(v)}" for k, v in seasons.items()) or "none"
    print(f"Wrote {OUT} — {summary}")


if __name__ == "__main__":
    main()
