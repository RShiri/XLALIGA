#!/usr/bin/env python3
"""Aggregate every shot into shots.js for the dashboard's Team Lab (shot map / xG
heatmap and the team style fingerprints).

Unlike the World Cup pipeline's build_shots.py (which reads the raw match JSONs),
this reads the already-shipped per-match detail files in ``matches_detail/*.js``
(``window.MATCH_DETAIL``) — the raw scrapes are git-ignored and not present in this
clean repo, but the detail files carry every shot with the same xG model + coords.

    window.LL_SHOTS = { "<season>": [{t,o,h,x,y,xg,g,ot,s,m}] }   — one entry per shot:
      t  team name        o  opponent name        h  True if the team was home
      x,y WhoScored coords (attacking -> x=100)    xg shot xG (same model as the PNGs)
      g  goal?   ot on target?   s situation (Open Play/Corner/Penalty/...)   m minute

Keyed by season (Spanish league: Aug-May, so a date's season starts in the July of
its calendar year). Only shots inside normal time are stored (the detail files
already exclude shootout kicks and own goals).
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DETAIL_DIR = os.path.join(HERE, "matches_detail")
OUT = os.path.join(HERE, "shots.js")


def season_of(date_str):
    """'2025-08-16' -> '2025-26'  ·  '2026-05-24' -> '2025-26'."""
    try:
        y, m, _ = date_str.split("-")
        y, m = int(y), int(m)
    except (ValueError, AttributeError):
        return "unknown"
    start = y if m >= 7 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def load_detail(path):
    """Parse a ``window.MATCH_DETAIL = {..};`` file into its dict."""
    text = open(path, encoding="utf-8").read()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        return None
    return json.loads(text[i:j + 1])


def main():
    by_season = {}
    files = sorted(glob.glob(os.path.join(DETAIL_DIR, "*.js")))
    for path in files:
        d = load_detail(path)
        if not d or not d.get("shots"):
            continue
        home = (d.get("home") or {}).get("name", "")
        away = (d.get("away") or {}).get("name", "")
        season = season_of(d.get("date", ""))
        bucket = by_season.setdefault(season, [])
        for s in d["shots"]:
            side = s.get("team")
            if side == "home":
                t, o, h = home, away, True
            elif side == "away":
                t, o, h = away, home, False
            else:
                continue
            bucket.append({
                "t": t, "o": o, "h": h,
                "x": round(s.get("x", 0), 1), "y": round(s.get("y", 0), 1),
                "xg": round(s.get("xg") or 0, 3),
                "g": bool(s.get("goal")),
                "ot": bool(s.get("onTarget")),
                "s": s.get("sit") or "Open Play",
                "m": s.get("min") or 0,
            })

    payload = json.dumps(by_season, separators=(",", ":"), ensure_ascii=False)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.LL_SHOTS = " + payload + ";\n")

    total = sum(len(v) for v in by_season.values())
    print(f"shots.js: {total} shots across {len(files)} match files "
          f"-> seasons {sorted(by_season)}")


if __name__ == "__main__":
    main()
