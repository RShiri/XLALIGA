#!/usr/bin/env python3
"""Export the full WC2026 dataset as a downloadable database.

Writes to laliga_dashboard/database/:
  results.csv            one row per match (score, xG, venue, stage)
  team_match_stats.csv   one row per team per match (possession, shots, passes, …)
  player_match_stats.csv one row per player per match
  players.csv            aggregated career-so-far player totals
  standings.csv          group tables
  laliga.sqlite          all of the above as SQL tables

Pulls from both scraped sources (FotMob match stats + WhoScored player/event data)
via the existing builders, so the exports stay in lock-step with the website.
"""
import csv
import json
import os
import sys
import glob
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_players
from build_match_details import norm, is_match_file
from xg_model import team_xg_from_events

MATCH_DIR = os.path.join(ROOT, "laliga", "matches")
OUT_DIR = os.path.join(HERE, "database")


def _load_data_js():
    """Reuse the already-built data.js for results + standings."""
    path = os.path.join(HERE, "data.js")
    txt = open(path, encoding="utf-8").read()
    return json.loads(txt.split("= ", 1)[1].rstrip().rstrip(";\n").rstrip(";"))


def results_rows(data):
    for m in data["matches"]:
        if not m["played"]:
            continue
        yield dict(match_id=m["id"], date=m["date"], stage=m.get("stage", ""),
                   venue=m.get("venue", ""), home=m["home"], away=m["away"],
                   home_score=m["hs"], away_score=m["as"],
                   home_xg=m["xg_home"], away_xg=m["xg_away"],
                   xg_estimated=int(bool(m.get("xg_estimated"))))


def team_match_stat_rows(data):
    keys = ["possession", "shots", "sot", "passes", "pass_acc", "big_chances",
            "big_missed", "saves", "fouls", "duels_won", "xg"]
    for m in data["matches"]:
        if not m["played"] or not m.get("has_stats"):
            continue
        s = m["stats"]
        for i, side in enumerate(("home", "away")):
            team = m["home"] if side == "home" else m["away"]
            opp = m["away"] if side == "home" else m["home"]
            row = dict(match_id=m["id"], date=m["date"], team=team, opponent=opp,
                       side=side, goals=(m["hs"] if side == "home" else m["as"]),
                       conceded=(m["as"] if side == "home" else m["hs"]))
            for k in keys:
                pair = s.get(k) or [None, None]
                row[k] = pair[i]
            yield row


def team_match_stat_by_source_rows(data):
    """One row per team per match PER SOURCE — the raw provider numbers behind the
    averaged team_match_stats (so all scraped data is stored, not just the mean)."""
    keys = ["possession", "shots", "sot", "passes", "pass_acc", "big_chances",
            "big_missed", "saves", "fouls", "duels_won", "xg", "corners"]
    for m in data["matches"]:
        if not m["played"]:
            continue
        for source, line in (m.get("statsBySource") or {}).items():
            for i, side in enumerate(("home", "away")):
                team = m["home"] if side == "home" else m["away"]
                opp = m["away"] if side == "home" else m["home"]
                row = dict(match_id=m["id"], date=m["date"], team=team, opponent=opp,
                           side=side, source=source,
                           goals=(m["hs"] if side == "home" else m["as"]),
                           conceded=(m["as"] if side == "home" else m["hs"]))
                for k in keys:
                    pair = line.get(k) or [None, None]
                    row[k] = pair[i]
                yield row


def standings_rows(data):
    # League table is a single flat, pre-ranked array (no groups).
    for r in data["standings"]:
        yield dict(position=r.get("rank"), team=r["team"], played=r["P"],
                   won=r["W"], drawn=r["D"], lost=r["L"], gf=r["GF"], ga=r["GA"],
                   gd=r["GD"], points=r["Pts"])


def _write_csv(name, rows):
    rows = list(rows)
    path = os.path.join(OUT_DIR, name)
    if not rows:
        open(path, "w").close()
        return 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _write_sqlite(tables):
    path = os.path.join(OUT_DIR, "laliga.sqlite")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    cur = con.cursor()
    for name, rows in tables.items():
        rows = list(rows)
        if not rows:
            continue
        cols = list(rows[0].keys())
        coldefs = ", ".join(f'"{c}" {_sql_type(rows, c)}' for c in cols)
        cur.execute(f'CREATE TABLE "{name}" ({coldefs})')
        collist = ", ".join(f'"{c}"' for c in cols)
        cur.executemany(
            f'INSERT INTO "{name}" ({collist}) VALUES ({", ".join("?" for _ in cols)})',
            [tuple(r.get(c) for c in cols) for r in rows])
    con.commit()
    con.close()
    return path


def _sql_type(rows, col):
    for r in rows:
        v = r.get(col)
        if v is None:
            continue
        if isinstance(v, bool):
            return "INTEGER"
        if isinstance(v, int):
            return "INTEGER"
        if isinstance(v, float):
            return "REAL"
        return "TEXT"
    return "TEXT"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = _load_data_js()
    # data.js is season-keyed now; export the default (current) season's tables.
    data = payload["seasons"][payload["defaultSeason"]]
    data["generated"] = payload.get("generated", "")

    results = list(results_rows(data))
    team_stats = list(team_match_stat_rows(data))
    team_stats_src = list(team_match_stat_by_source_rows(data))
    standings = list(standings_rows(data))
    player_match = list(build_players.per_match_rows())
    players = build_players.aggregate()

    counts = {
        "results.csv": _write_csv("results.csv", results),
        "team_match_stats.csv": _write_csv("team_match_stats.csv", team_stats),
        "team_match_stats_by_source.csv": _write_csv("team_match_stats_by_source.csv", team_stats_src),
        "player_match_stats.csv": _write_csv("player_match_stats.csv", player_match),
        "players.csv": _write_csv("players.csv", players),
        "standings.csv": _write_csv("standings.csv", standings),
    }
    _write_sqlite({
        "matches": results,
        "team_match_stats": team_stats,
        "team_match_stats_by_source": team_stats_src,
        "player_match_stats": player_match,
        "players": players,
        "standings": standings,
    })

    # a small manifest the Data tab reads to list downloads + counts
    raw_files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(MATCH_DIR, "*.json"))
                       if is_match_file(f))
    manifest = {
        "generated": data.get("generated", ""),
        "tables": [
            {"file": "results.csv", "label": "Game results", "rows": counts["results.csv"]},
            {"file": "team_match_stats.csv", "label": "Team stats per game (averaged)", "rows": counts["team_match_stats.csv"]},
            {"file": "team_match_stats_by_source.csv", "label": "Team stats per game (per source)", "rows": counts["team_match_stats_by_source.csv"]},
            {"file": "player_match_stats.csv", "label": "Player stats per game", "rows": counts["player_match_stats.csv"]},
            {"file": "players.csv", "label": "Player aggregate totals", "rows": counts["players.csv"]},
            {"file": "standings.csv", "label": "League standings", "rows": counts["standings.csv"]},
        ],
        "sqlite": "laliga.sqlite",
        "raw_match_files": len(raw_files),
        "raw_match_dir": "../laliga/matches/",
    }
    with open(os.path.join(OUT_DIR, "manifest.js"), "w", encoding="utf-8") as fh:
        fh.write("window.WC_DATABASE = ")
        json.dump(manifest, fh, ensure_ascii=False)
        fh.write(";\n")

    print("Database exported to", OUT_DIR)
    for k, v in counts.items():
        print(f"  {k}: {v} rows")
    print(f"  laliga.sqlite + manifest.js · {len(raw_files)} raw match files")


if __name__ == "__main__":
    main()
