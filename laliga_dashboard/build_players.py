#!/usr/bin/env python3
"""Aggregate per-player statistics across every played match into players.js.

Combines both data sources the pipeline scrapes: WhoScored player stat streams
(passes, shots, tackles, ratings, …) and the event feed (goals, assists, cards,
minutes). Writes window.WC_PLAYERS for the dashboard's Players tab, and exposes
aggregate()/per_match_rows() for the database exporter.
"""
import json
import glob
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from build_match_details import norm, _match_extras, _player_rating, is_match_file
from xg_model import (ascii_name, SHOT_TYPES, shot_xg, match_xg_by_event,
                      is_shootout, player_xa_from_events)

MATCH_DIR = os.environ.get("LALIGA_MATCH_DIR") or os.path.join(ROOT, "laliga", "matches")
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
    rec = dict(pid=pid, name=name, team=team, pos=pos, photo=None,
               mp=0, starts=0, mins=0, g=0, a=0, yc=0, rc=0, pen=0,
               rating_sum=0.0, rating_n=0, rating_best=0.0, xg=0.0, xa=0.0,
               npxg=0.0, bc_missed=0, bc_missed_xg=0.0,
               gk_shots_faced=0, gk_xgot_faced=0.0, gk_goals_conceded=0,
               prog_passes=0, prog_carries=0)
    for v in SUM_STATS.values():
        rec[v] = 0.0
    return rec


def _norm_player(name):
    """Loose match key for a player name across providers (accents/case/whitespace
    differ — e.g. WhoScored's 'Kylian Mbappe' vs FotMob's 'K. Mbappe'). Keeps only the
    last token (surname), which varies least across providers' naming conventions."""
    a = ascii_name(name or "").lower().strip()
    parts = [p for p in a.replace(".", " ").split() if p]
    return parts[-1] if parts else a


def _fotmob_photo_lookup(match_data):
    """(side, normalized surname) -> FotMob player id, for headshot URLs.

    No shared player id exists across providers, so this matches by team side +
    surname — best-effort, not guaranteed 1:1. A surname that appears more than once
    on the same side in the same match is dropped rather than guessed at: a wrong
    photo is worse than a missing one."""
    counts, ids = {}, {}
    for entry in match_data.get("_fotmob_player_ids") or []:
        fmid = entry.get("fotmob_id")
        if fmid is None:
            continue
        key = (entry.get("team"), _norm_player(entry.get("player")))
        counts[key] = counts.get(key, 0) + 1
        ids[key] = fmid
    return {k: v for k, v in ids.items() if counts[k] == 1}


def _fotmob_xgot_buckets(match_data):
    """(side, normalized surname, minute) -> [xgot, ...] from FotMob's own shotmap
    (_fotmob_shots — see laliga/scraper.py's _fotmob_shot_xg_list), for the
    goalkeeper goals-prevented stat below. Same best-effort team+surname+minute
    matching as build_match_details.py's cross-provider xG buckets."""
    out = {}
    for s in match_data.get("_fotmob_shots") or []:
        xgot = s.get("xgot")
        if xgot is None:
            continue
        key = (s.get("team"), _norm_player(s.get("player")), s.get("min", 0))
        out.setdefault(key, []).append(xgot)
    return out


def _pop_xgot(buckets, side, player, minute):
    key = (side, _norm_player(player), minute)
    vals = buckets.get(key)
    if not vals:
        for m in (minute - 1, minute + 1):
            vals = buckets.get((side, _norm_player(player), m))
            if vals:
                break
    return vals.pop(0) if vals else None


def _keeper_windows(side_data, ex):
    """[(playerId, on_min, off_min), ...] for this side's keeper(s), ordered by
    on_min. off_min is None if that keeper played to the final whistle."""
    windows = []
    for p in side_data.get("players", []):
        if p.get("position") != "GK":
            continue
        pid = p.get("playerId")
        started = bool(p.get("isFirstEleven"))
        on_m = 0 if started else ex["on_min"].get(pid)
        if on_m is None:
            continue  # bench keeper who never came on
        windows.append((pid, on_m, ex["off_min"].get(pid)))
    windows.sort(key=lambda w: w[1])
    return windows


def _keeper_at_minute(windows, minute):
    for pid, on_m, off_m in windows:
        if minute >= on_m and (off_m is None or minute <= off_m):
            return pid
    return windows[-1][0] if windows else None


def _keeper_shot_extras(match_data, ex):
    """Facing-keeper playerId -> dict(shots_faced, xgot_faced, goals_conceded), for
    the goalkeeper goals-prevented stat: Sigma xGOT faced minus goals actually
    conceded. xGOT comes from FotMob (_fotmob_shots) since neither our own model
    nor WhoScored's placement is wired into the build pipeline yet — coverage is
    partial (not every shot matches a FotMob one), same partial-coverage caveat as
    every other FotMob cross-provider number in this codebase. Own goals are
    excluded entirely (not a shot faced by the opposing keeper at all, same as
    every shot-based xG stat elsewhere excludes them)."""
    home, away = match_data.get("home", {}), match_data.get("away", {})
    home_tid, away_tid = home.get("teamId"), away.get("teamId")
    opposite = {"home": "away", "away": "home"}
    windows = {"home": _keeper_windows(home, ex), "away": _keeper_windows(away, ex)}
    fm_buckets = _fotmob_xgot_buckets(match_data)
    pid_name = match_data.get("playerIdNameDictionary", {})
    out = {}
    for ev in match_data.get("events", []):
        t = ev.get("type", {})
        tname = t.get("displayName") if isinstance(t, dict) else None
        if tname not in ("Goal", "SavedShot"):
            continue
        if is_shootout(ev):
            continue
        quals = {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}
        if "OwnGoal" in quals:
            continue
        tid = ev.get("teamId")
        side = "home" if tid == home_tid else "away" if tid == away_tid else None
        if side is None:
            continue
        facing_side = opposite[side]
        minute = ev.get("minute", 0)
        kpid = _keeper_at_minute(windows[facing_side], minute)
        if kpid is None:
            continue
        rec = out.setdefault(kpid, dict(shots_faced=0, xgot_faced=0.0, goals_conceded=0))
        rec["shots_faced"] += 1
        if tname == "Goal":
            rec["goals_conceded"] += 1
        shot_player = pid_name.get(str(ev.get("playerId")), "")
        xgot = _pop_xgot(fm_buckets, side, shot_player, minute)
        if xgot is not None:
            rec["xgot_faced"] += xgot
    return out


_PROG_BOX_X, _PROG_BOX_Y0, _PROG_BOX_Y1 = 85.0, 22.5, 77.5  # WhoScored 0-100 pitch coords


def _is_progressive(x, y, ex, ey):
    """StatsBomb's public progressive-action definition: >=25% closer to goal if it
    started in the own half, >=10% if in the attacking half, or ends inside the box
    regardless. WhoScored's x/y are already oriented to the attacking team's
    perspective (goal fixed at (100, 50) for both sides), same convention the
    shot/xG code relies on -- no home/away flip needed."""
    d0 = math.hypot(100.0 - x, 50.0 - y)
    if d0 <= 0:
        return False
    d1 = math.hypot(100.0 - ex, 50.0 - ey)
    need = 0.25 if x < 50 else 0.10
    return (d0 - d1) / d0 >= need or (ex >= _PROG_BOX_X and _PROG_BOX_Y0 <= ey <= _PROG_BOX_Y1)


def _progressive_actions(match_data):
    """playerId -> dict(prog_passes, prog_carries) for the match.

    Carries (WhoScored's "TakeOn" event) have no end coordinate on the event
    itself -- inferred the same way build_match_details.py's dribble map already
    does: the same player's next on-ball touch within 7 seconds."""
    events = match_data.get("events", [])
    out = {}
    for i, ev in enumerate(events):
        t = ev.get("type", {})
        tname = t.get("displayName") if isinstance(t, dict) else None
        pid = ev.get("playerId")
        if pid is None or ev.get("outcomeType", {}).get("displayName") != "Successful":
            continue
        if tname == "Pass":
            x, y = ev.get("x", 0), ev.get("y", 0)
            ex_, ey_ = ev.get("endX", x), ev.get("endY", y)
            if _is_progressive(x, y, ex_, ey_):
                out.setdefault(pid, dict(prog_passes=0, prog_carries=0))["prog_passes"] += 1
        elif tname == "TakeOn":
            x, y = ev.get("x", 0), ev.get("y", 0)
            t0 = (ev.get("minute") or 0) * 60 + (ev.get("second") or 0)
            ex_ = ey_ = None
            for nxt in events[i + 1:]:
                if ((nxt.get("minute") or 0) * 60 + (nxt.get("second") or 0)) - t0 > 7:
                    break
                if nxt.get("playerId") == pid and nxt.get("x") is not None:
                    ex_, ey_ = nxt.get("x"), nxt.get("y") or 0
                    break
            if ex_ is not None and _is_progressive(x, y, ex_, ey_):
                out.setdefault(pid, dict(prog_passes=0, prog_carries=0))["prog_carries"] += 1
    return out


def _player_shot_extras(match_data):
    """playerId -> dict(xg, npxg, pen, bc_missed, bc_missed_xg) for the match.

    npxg excludes penalty shots (their xG is the fixed penalty constant, not a
    finishing signal). bc_missed/bc_missed_xg are shots flagged BigChance by
    Opta/WhoScored that did NOT end in a goal — count and the xG "left on the
    table", used for the forward-efficiency stat: npg / (npxg + bc_missed_xg)."""
    xg_by_event = match_xg_by_event(match_data)   # score the whole match once (v3)
    out = {}
    for ev in match_data.get("events", []):
        t = ev.get("type", {})
        tname = t.get("displayName") if isinstance(t, dict) else None
        if tname not in SHOT_TYPES:
            continue
        if is_shootout(ev):
            continue  # exclude penalty-shootout kicks from player xG
        pid = ev.get("playerId")
        if pid is None:
            continue
        xg, meta = shot_xg(ev, xg_by_event)
        rec = out.setdefault(pid, dict(xg=0.0, npxg=0.0, pen=0, bc_missed=0, bc_missed_xg=0.0))
        rec["xg"] += xg
        if meta["penalty"]:
            if tname == "Goal":
                rec["pen"] += 1
        else:
            rec["npxg"] += xg
        if meta["big_chance"] and tname != "Goal":
            rec["bc_missed"] += 1
            rec["bc_missed_xg"] += xg
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
        shot_extras = _player_shot_extras(d)
        keeper_extras = _keeper_shot_extras(d, ex)
        prog_map = _progressive_actions(d)
        xa_map = player_xa_from_events(d)
        photo_lookup = _fotmob_photo_lookup(d)
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
                if not rec.get("photo"):
                    fmid = photo_lookup.get((side, _norm_player(p.get("name", ""))))
                    if fmid:
                        rec["photo"] = f"https://images.fotmob.com/image_resources/playerimages/{fmid}.png"
                rec["mp"] += 1
                if started:
                    rec["starts"] += 1
                    rec["mins"] += (ex["off_min"].get(pid) if ex["off_min"].get(pid) is not None else ex["end_min"])
                else:
                    rec["mins"] += max(0, ex["end_min"] - on_m)
                sx = shot_extras.get(pid, {})
                rec["g"] += ex["goals"].get(pid, 0)
                rec["a"] += ex["assists"].get(pid, 0)
                rec["yc"] += ex["yellow"].get(pid, 0)
                rec["rc"] += ex["red"].get(pid, 0)
                rec["pen"] += sx.get("pen", 0)
                rec["xg"] += sx.get("xg", 0.0)
                rec["npxg"] += sx.get("npxg", 0.0)
                rec["bc_missed"] += sx.get("bc_missed", 0)
                rec["bc_missed_xg"] += sx.get("bc_missed_xg", 0.0)
                rec["xa"] += xa_map.get(pid, 0.0)
                kx = keeper_extras.get(pid)
                if kx:
                    rec["gk_shots_faced"] += kx["shots_faced"]
                    rec["gk_xgot_faced"] += kx["xgot_faced"]
                    rec["gk_goals_conceded"] += kx["goals_conceded"]
                pa = prog_map.get(pid)
                if pa:
                    rec["prog_passes"] += pa["prog_passes"]
                    rec["prog_carries"] += pa["prog_carries"]
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
        r["xa"] = round(r["xa"], 2)
        r["xg_diff"] = round(r["g"] - r["xg"], 2)
        r["xa_diff"] = round(r["a"] - r["xa"], 2)   # assists over/under expected
        r["xgi"] = round(r["xg"] + r["xa"], 2)       # xG involvement (xG + xA)
        r["npg"] = r["g"] - r["pen"]                 # non-penalty goals
        r["npxg"] = round(r["npxg"], 2)
        r["bc_missed_xg"] = round(r["bc_missed_xg"], 2)
        # Forward efficiency: non-penalty goals vs. the chance quality on offer —
        # npxG (every non-pen shot) plus the xG value of big chances NOT converted
        # (the "left on the table" portion big chances-missed alone doesn't price).
        # >1.0 = finishing above what the chances on offer were worth; <1.0 = wasteful.
        _fwd_denom = r["npxg"] + r["bc_missed_xg"]
        r["fwd_eff"] = round(r["npg"] / _fwd_denom, 2) if _fwd_denom > 0.05 else None
        # Goals prevented: Sigma xGOT faced minus goals actually conceded (own goals
        # excluded from both sides, per _keeper_shot_extras). Positive = shot-stopping
        # above what the shots faced were worth; negative = below. None for outfielders
        # and keepers who never faced a matched shot (partial FotMob xGOT coverage).
        r["gk_xgot_faced"] = round(r["gk_xgot_faced"], 2)
        r["gk_goals_prevented"] = (round(r["gk_xgot_faced"] - r["gk_goals_conceded"], 2)
                                   if r["pos"] == "GK" and r["gk_shots_faced"] else None)
        for v in list(SUM_STATS.values()) + ["mins"]:
            r[v] = int(round(r[v]))
        r.pop("rating_sum", None)
        r.pop("rating_n", None)
        out.append(r)
    out.sort(key=lambda r: (-r["ga"], -r["g"], -(r["rating"] or 0)))
    return out


def per_match_rows(match_dir=MATCH_DIR):
    """Yield one flat dict per player per match (for the database export)."""
    for mid, d in _iter_played(match_dir):
        ex = _match_extras(d)
        shot_extras = _player_shot_extras(d)
        xa_map = player_xa_from_events(d)
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
                sx = shot_extras.get(pid, {})
                row = dict(match_id=mid, date=date, team=team, opponent=opp,
                           player_id=pid, player=ascii_name(p.get("name", "")),
                           position=p.get("position", ""), started=int(started), minutes=int(mins),
                           goals=ex["goals"].get(pid, 0), assists=ex["assists"].get(pid, 0),
                           yellow=ex["yellow"].get(pid, 0), red=ex["red"].get(pid, 0),
                           penalties=sx.get("pen", 0),
                           rating=_player_rating(p), xg=round(sx.get("xg", 0.0), 2),
                           npxg=round(sx.get("npxg", 0.0), 2),
                           bc_missed=sx.get("bc_missed", 0),
                           bc_missed_xg=round(sx.get("bc_missed_xg", 0.0), 2),
                           xa=round(xa_map.get(pid, 0.0), 2))
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
