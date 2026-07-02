#!/usr/bin/env python3
"""Build per-match detail files for the interactive match dashboard.

For every match that has WhoScored events, extract the full shot list (with the
same xG model as the PNG renderer), every pass with its end coordinates, the goal
timeline and the line-ups. Each match is written to matches_detail/<id>.js as

    window.MATCH_DETAIL = {...}

so match.html can load one game via a plain <script> tag (works on file:// too).

Usage:
    py laliga_dashboard/build_match_details.py            # build all
    (the renderer hook calls build_one(match_data) for the game it just rendered)
"""
import json
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from xg_model import (SHOT_TYPES, shot_xg, player_full_name, ascii_name, is_shootout,
                      player_xa_from_events)

MATCH_DIR = os.path.join(ROOT, "laliga", "matches")
OUT_DIR = os.path.join(HERE, "matches_detail")

NAME_MAP = {
    "European Play-Off A": "Bosnia and Herzegovina",
    "European Play-Off B": "Sweden",
    "European Play-Off C": "Turkiye",
    "European Play-Off D": "Czechia",
    "FIFA Play-Off Tournament 2": "Iraq",
}


import re as _re
# La Liga saves matches by FotMob id (e.g. 4837113.json); the WC pipeline used
# date_Home_vs_Away.json. Accept either, excluding the scraper's match_<id>_cache.json.
_MATCH_NAME_RE = _re.compile(r"^(\d+|\d{4}_\d{2}_\d{2}_.+_vs_.+)$")


def is_match_file(path):
    """True for real match JSONs (YYYY_MM_DD_Home_vs_Away.json), excluding the
    scraper's WhoScored cache files (match_<id>_cache.json) that share the folder."""
    return bool(_MATCH_NAME_RE.match(os.path.basename(path)[:-5]))


def norm(name):
    return NAME_MAP.get(name, name)


def _team_color(name):
    try:
        from laliga.team_colors import get_team_colors
        c = get_team_colors(name, fallback_home=True)
        return c.get("primary", "#4ea1ff")
    except Exception:
        return "#4ea1ff"


def _match_extras(match_data):
    """Per-playerId goals, assists, cards and sub on/off minutes from events."""
    goals, assists, yellow, red, on_min, off_min = {}, {}, {}, {}, {}, {}
    end_min = 90
    for e in match_data.get("events", []):
        if is_shootout(e):
            continue  # shootout kicks aren't goals and must not extend the timeline
        m = e.get("minute") or 0
        if m > end_min:
            end_min = m
        pid = e.get("playerId")
        if pid is None:
            continue
        t = e.get("type", {}).get("displayName", "")
        quals = {q.get("type", {}).get("displayName", "") for q in e.get("qualifiers", [])}
        if t == "Goal" and not (e.get("isOwnGoal") or "OwnGoal" in quals):
            goals[pid] = goals.get(pid, 0) + 1
        if "IntentionalGoalAssist" in quals:
            assists[pid] = assists.get(pid, 0) + 1
        if t == "Card":
            if "Red" in quals or "SecondYellow" in quals:
                red[pid] = red.get(pid, 0) + 1
                if "SecondYellow" in quals:
                    yellow[pid] = yellow.get(pid, 0) + 1
            elif "Yellow" in quals:
                yellow[pid] = yellow.get(pid, 0) + 1
        if t == "SubstitutionOn":
            on_min[pid] = m
        elif t == "SubstitutionOff":
            off_min[pid] = m
    return dict(goals=goals, assists=assists, yellow=yellow, red=red,
                on_min=on_min, off_min=off_min, end_min=end_min)


def _player_rating(p):
    rd = (p.get("stats") or {}).get("ratings") or {}
    if not rd:
        return None
    try:
        return round(float(list(rd.values())[-1]), 1)
    except (TypeError, ValueError):
        return None


def _lineup(side, ex, xg_map=None, xa_map=None):
    xg_map = xg_map or {}
    xa_map = xa_map or {}
    starters, subs = [], []
    for p in side.get("players", []):
        pid = p.get("playerId")
        starter = bool(p.get("isFirstEleven"))
        on_m = ex["on_min"].get(pid)
        off_m = ex["off_min"].get(pid)
        # minutes played
        if starter:
            mins = (off_m if off_m is not None else ex["end_min"])
        elif on_m is not None:
            mins = ex["end_min"] - on_m
        else:
            mins = 0
        entry = {
            "num": p.get("shirtNo"),
            "name": ascii_name(p.get("name", "")),
            "pos": p.get("position", ""),
            "motm": bool(p.get("isManOfTheMatch")),
            "rating": _player_rating(p),
            "g": ex["goals"].get(pid, 0),
            "a": ex["assists"].get(pid, 0),
            "xg": round(xg_map.get(pid, 0.0), 2),
            "xa": round(xa_map.get(pid, 0.0), 2),
            "yc": ex["yellow"].get(pid, 0),
            "rc": ex["red"].get(pid, 0),
            "on": on_m,
            "off": off_m,
            "mins": max(0, mins),
        }
        (starters if starter else subs).append(entry)
    # only keep subs that actually came on
    subs = [s for s in subs if s["on"] is not None]
    subs.sort(key=lambda s: s["on"])
    return {"starters": starters, "subs": subs}


_SHOOTOUT_OUTCOME = {
    "Goal": "goal",
    "SavedShot": "saved",
    "MissedShots": "missed",
    "ShotOnPost": "post",
}


def _shootout(match_data, side_of):
    """Per-kick penalty-shootout list (empty for games without a shootout).

    Each kick carries the goal-mouth landing spot (GoalMouthY/Z qualifiers) so the
    dashboard can plot where every penalty was placed inside the goal frame and which
    were scored / saved / missed. The opposing keeper is linked to saved kicks via the
    WhoScored OppositeRelatedEvent qualifier on the Save/PenaltyFaced event."""
    events = match_data.get("events", [])
    sh_events = [e for e in events if is_shootout(e)]
    if not sh_events:
        return []
    # eventId of the shooter -> keeper who faced (and saved) it
    keeper_by_shot = {}
    for e in sh_events:
        t = e.get("type", {}).get("displayName", "")
        if t not in ("Save", "PenaltyFaced") or e.get("playerId") is None:
            continue
        for q in e.get("qualifiers", []):
            if q.get("type", {}).get("displayName") == "OppositeRelatedEvent":
                keeper_by_shot[str(q.get("value"))] = player_full_name(match_data, e.get("playerId"))
    pens, order = [], 0
    for e in sh_events:
        t = e.get("type", {}).get("displayName", "")
        if t not in _SHOOTOUT_OUTCOME:
            continue
        side = side_of.get(e.get("teamId"))
        if side is None:
            continue
        gy = gz = None
        for q in e.get("qualifiers", []):
            dn = q.get("type", {}).get("displayName")
            try:
                if dn == "GoalMouthY":
                    gy = round(float(q.get("value")), 1)
                elif dn == "GoalMouthZ":
                    gz = round(float(q.get("value")), 1)
            except (TypeError, ValueError):
                pass
        order += 1
        pens.append({
            "team": side,
            "order": order,
            "player": player_full_name(match_data, e.get("playerId")),
            "outcome": _SHOOTOUT_OUTCOME[t],
            "gy": gy,
            "gz": gz,
            "keeper": keeper_by_shot.get(str(e.get("eventId"))),
        })
    return pens


def find_png(match_id):
    """Relative path (from the dashboard folder) to the rendered infographic, or None.

    Always emits the published LaLiga path (where the deploy copies the PNG),
    even when the file currently only exists in the git-ignored laliga/output. A
    fresh auto-deploy regenerates data.js *before* the PNG reaches LaLiga,
    so returning the output/ path here would 404 the Infographic link on the live
    site. The PNG is checked in both places only to decide whether the match has a
    render at all."""
    published = os.path.join("laliga_png", match_id + ".png")
    for rel in (published, os.path.join("laliga", "output", match_id + ".png")):
        if os.path.exists(os.path.join(ROOT, rel)):
            return "../" + published.replace("\\", "/")
    return None


def extract(match_data):
    """Build the detail dict for one match (assumes it has events)."""
    home, away = match_data.get("home", {}), match_data.get("away", {})
    hid, aid = home.get("teamId"), away.get("teamId")
    side_of = {hid: "home", aid: "away"}
    ex = _match_extras(match_data)
    # per-player shot xG + expected assists (xA) for the line-up cards
    xa_map = player_xa_from_events(match_data)
    xg_map = {}
    for _ev in match_data.get("events", []):
        _t = _ev.get("type", {})
        if not isinstance(_t, dict) or _t.get("displayName") not in SHOT_TYPES:
            continue
        if is_shootout(_ev) or _ev.get("playerId") is None:
            continue
        _xg, _ = shot_xg(_ev)
        xg_map[_ev["playerId"]] = xg_map.get(_ev["playerId"], 0.0) + _xg

    # Receiver of each successful pass = the next same-team successful event's player
    # (mirrors the renderer's pass-network method). Keyed by event index.
    events = match_data.get("events", [])
    receiver = {}
    for i, ev in enumerate(events):
        t = ev.get("type", {})
        if not isinstance(t, dict) or t.get("displayName") != "Pass":
            continue
        if ev.get("outcomeType", {}).get("displayName") != "Successful":
            continue
        tid = ev.get("teamId")
        for j in range(i + 1, min(i + 6, len(events))):
            nxt = events[j]
            if nxt.get("teamId") != tid:
                break
            if nxt.get("outcomeType", {}).get("displayName") == "Successful" \
                    and nxt.get("playerId") is not None:
                receiver[i] = player_full_name(match_data, nxt.get("playerId"))
                break

    shots, passes, goals, dribbles, saves = [], [], [], [], []
    max_min = 0
    for _i, ev in enumerate(events):
        tid = ev.get("teamId")
        side = side_of.get(tid)
        if side is None:
            continue
        if is_shootout(ev):
            continue  # penalty shootout — exclude its kicks from shots/goals/maps/timeline
        tname = ev.get("type", {})
        tname = tname.get("displayName") if isinstance(tname, dict) else ""
        minute = ev.get("minute", 0)
        if minute and minute > max_min:
            max_min = minute

        if tname in SHOT_TYPES:
            ev_quals = {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}
            is_own_goal = bool(ev.get("isOwnGoal")) or ("OwnGoal" in ev_quals)
            if tname == "Goal" and is_own_goal:
                # Own goal: counts for the OPPONENT (the beneficiary), and is NOT a shot
                # by either side. The raw event sits at the scorer's own-goal end with the
                # conceding team's id, which previously credited the wrong team and drew a
                # cross-pitch line on the All Goals Map. Record a goal-timeline entry for the
                # opponent and skip the shot list so shot maps / xG / All Goals Map stay clean.
                goals.append({
                    "team": "away" if side == "home" else "home",
                    "min": minute,
                    "scorer": player_full_name(match_data, ev.get("playerId")),
                    "assist": None,
                    "pen": False,
                    "own": True,
                    # coords mirrored 180° into the beneficiary's frame so the All Goals
                    # Map plots the own goal at the end they were attacking (not across it)
                    "x": round(100 - ev.get("x", 0), 1),
                    "y": round(100 - ev.get("y", 0), 1),
                })
                continue
            xg, meta = shot_xg(ev)
            # goal-mouth landing spot (where the shot crossed/was aimed at the goal line):
            # GoalMouthY = across the goal, GoalMouthZ = height. Powers the on-target
            # "where in the goal" map. Absent on some events → null.
            gy = gz = None
            for q in ev.get("qualifiers", []):
                dn = q.get("type", {}).get("displayName")
                try:
                    if dn == "GoalMouthY":
                        gy = round(float(q.get("value")), 1)
                    elif dn == "GoalMouthZ":
                        gz = round(float(q.get("value")), 1)
                except (TypeError, ValueError):
                    pass
            shots.append({
                "team": side,
                "x": round(ev.get("x", 0), 1),
                "y": round(ev.get("y", 0), 1),
                "min": minute,
                "sec": ev.get("second", 0),
                "player": player_full_name(match_data, ev.get("playerId")),
                "xg": xg,
                "goal": tname == "Goal",
                "onTarget": tname in ("Goal", "SavedShot"),
                "blocked": tname == "BlockedShot",
                "post": tname == "ShotOnPost",
                "body": meta["body"],
                "sit": meta["situation"],
                "big": meta["big_chance"],
                "gy": gy,
                "gz": gz,
            })
            if tname == "Goal":
                goals.append({
                    "team": side,
                    "min": minute,
                    "scorer": player_full_name(match_data, ev.get("playerId")),
                    "assist": (player_full_name(match_data, ev.get("relatedPlayerId"))
                               if ev.get("relatedPlayerId") else None),
                    "pen": meta["penalty"],
                    "own": False,
                })
        elif tname == "Pass":
            quals = {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}
            ok = ev.get("outcomeType", {}).get("displayName") == "Successful"
            x, y = ev.get("x", 0), ev.get("y", 0)
            ex_, ey_ = ev.get("endX", x), ev.get("endY", y)
            # progressive = meaningful forward advance toward the opponent goal
            prog = (ex_ - x) >= 15
            passes.append({
                "team": side,
                "x": round(x, 1),
                "y": round(y, 1),
                "ex": round(ex_, 1),
                "ey": round(ey_, 1),
                "min": minute,
                "sec": ev.get("second", 0),
                "player": player_full_name(match_data, ev.get("playerId")),
                "recv": receiver.get(_i),
                "ok": ok,
                "key": "KeyPass" in quals,
                "assist": "IntentionalGoalAssist" in quals,
                "cross": "Cross" in quals,
                "through": "Throughball" in quals,
                "prog": prog,
            })

        elif tname == "TakeOn":   # dribble / take-on (point event, success = beat the man)
            dx, dy = ev.get("x", 0), ev.get("y", 0)
            d_entry = {
                "team": side,
                "x": round(dx, 1),
                "y": round(dy, 1),
                "min": minute,
                "sec": ev.get("second", 0),
                "player": player_full_name(match_data, ev.get("playerId")),
                "ok": ev.get("outcomeType", {}).get("displayName") == "Successful",
            }
            # The feed gives no end coordinate for a take-on, so the carry destination is the
            # SAME player's next on-ball event (any type — pass, ball-touch, recovery, …)
            # within ~7s. The dashboard uses ex/ey to draw a direction arrow.
            pid = ev.get("playerId")
            t0 = minute * 60 + (ev.get("second") or 0)
            for nxt in events[_i + 1:]:
                if (nxt.get("minute") or 0) * 60 + (nxt.get("second") or 0) - t0 > 7:
                    break
                if nxt.get("playerId") == pid and nxt.get("x") is not None:
                    nx, ny = nxt.get("x"), nxt.get("y") or 0
                    if abs(nx - dx) > 0.8 or abs(ny - dy) > 0.8:
                        d_entry["ex"], d_entry["ey"] = round(nx, 1), round(ny, 1)
                    break
            dribbles.append(d_entry)

        elif tname == "Save":     # goalkeeper save (point event) — powers the All Goals Map
            saves.append({        # rebound chain's grey keeper-save node
                "team": side,
                "x": round(ev.get("x", 0), 1),
                "y": round(ev.get("y", 0), 1),
                "min": minute,
                "sec": ev.get("second", 0),
                "player": player_full_name(match_data, ev.get("playerId")),
            })

    meta = match_data.get("meta", {})
    mid_date = meta.get("date", "")

    return {
        "home": {"name": norm(home.get("name", "Home")), "raw": home.get("name", ""),
                 "score": home.get("score"), "pens": home.get("penalty_score"),
                 "color": _team_color(home.get("name", ""))},
        "away": {"name": norm(away.get("name", "Away")), "raw": away.get("name", ""),
                 "score": away.get("score"), "pens": away.get("penalty_score"),
                 "color": _team_color(away.get("name", ""))},
        "date": mid_date,
        "venue": meta.get("venue", ""),
        "stage": meta.get("stage", ""),
        "maxMin": max_min,
        "shots": shots,
        "passes": passes,
        "dribbles": dribbles,
        "saves": saves,
        "goals": sorted(goals, key=lambda g: g["min"]),
        "shootout": _shootout(match_data, side_of),
        "lineups": {"home": _lineup(home, ex, xg_map, xa_map),
                    "away": _lineup(away, ex, xg_map, xa_map)},
    }


def _match_id_from_file(path):
    return os.path.basename(path)[:-5]


def write_detail(match_data, match_id=None):
    """Write matches_detail/<id>.js for one match. Returns the path or None."""
    if not (match_data.get("events")):
        return None
    if match_id is None:
        meta = match_data.get("meta", {})
        date = (meta.get("date") or "2026_06_01").replace("-", "_")
        h = match_data.get("home", {}).get("name", "Home").replace(" ", "_")
        a = match_data.get("away", {}).get("name", "Away").replace(" ", "_")
        match_id = f"{date}_{h}_vs_{a}"
    os.makedirs(OUT_DIR, exist_ok=True)
    detail = extract(match_data)
    detail["id"] = match_id
    detail["png"] = find_png(match_id)
    out = os.path.join(OUT_DIR, match_id + ".js")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("window.MATCH_DETAIL = ")
        json.dump(detail, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    built = 0
    index = []
    for f in sorted(glob.glob(os.path.join(MATCH_DIR, "**", "*.json"), recursive=True)):
        if not is_match_file(f):
            continue  # skip scraper cache files
        d = json.load(open(f, encoding="utf-8"))
        if not d.get("events") or d["home"].get("score") is None:
            continue  # only finished games with events get an interactive page
        mid = _match_id_from_file(f)
        write_detail(d, mid)
        index.append(mid)
        built += 1
    # index of which matches have a detail page (used by the main site to link)
    with open(os.path.join(OUT_DIR, "_index.js"), "w", encoding="utf-8") as fh:
        fh.write("window.MATCH_DETAIL_INDEX = ")
        json.dump(index, fh, ensure_ascii=False)
        fh.write(";\n")
    print(f"Wrote {built} match detail files to {OUT_DIR}")


if __name__ == "__main__":
    main()
