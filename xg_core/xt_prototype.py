# -*- coding: utf-8 -*-
"""xT (Expected Threat) prototype — PLAN_new_models.md item 5.

NOT a package yet, deliberately: the plan calls for prototyping this against one
season in a scratch script before committing to an xt_core/ shape, since it's a
genuinely different model family (a state-value model over a Markov chain of
zone-to-zone transitions) with no existing xg_core file to template from.

Method (Karun Singh's public formulation, the standard reference implementation):
  1. Grid the pitch into M x N zones (16x12 here, the usual choice).
  2. From the event stream, empirically estimate per zone z:
       shot_prob[z]  = P(a shot is taken from z | some action happens from z)
       goal_prob[z]  = P(goal | shot from z)                     (finishing quality)
       move_prob[z]  = 1 - shot_prob[z]
       transition[z][z'] = P(a completed move from z ends in z' | it doesn't end
                             in a shot) -- from completed passes + take-on carries
  3. Solve the fixed point (value iteration, ~20 rounds is enough to converge):
       xT[z] = shot_prob[z]*goal_prob[z] + move_prob[z] * sum_z' transition[z][z']*xT[z']
  4. Per-action value added = xT[end zone] - xT[start zone], for every completed
     pass and take-on-carry by the acting team. This is the "threat created" by
     that specific action -- summed per player/team it's a season xT-added total.

Usage:
    py -m xg_core.xt_prototype                    # La Liga 2025-26 by default
    py -m xg_core.xt_prototype --season 2024-25

RESULT (run 2026-09-15, La Liga 2025-26, 380 matches, 9,486 shots, 300,667 completed
passes+carries): the value surface itself checks out -- smooth gradient from 0.048
near the own goal line to 0.366 in the six-yard-box zone, 13/15 non-decreasing steps
along the central channel toward goal, no discontinuities. The engine (grid, Markov
transition estimation, fixed-point solve) is sound.

BUT the naive "raw season sum of per-action xT-added" leaderboard is dominated by
centre-backs and goalkeepers (Daley Blind, David Soria, Antonio Sivera, Pau Cubarsi,
...), not attacking creators -- a well-documented real caveat of plain Karun Singh
xT: high-volume, low-risk buildup passing from deep zones (where each pass only adds
a tiny xT increment) accumulates more total season value than fewer, higher-value,
higher-risk passes further forward. This is NOT a bug in the value surface; it's a
property of summing an unweighted per-action metric over wildly different attempt
volumes and roles. Before this is presentable as a dashboard stat (a "creativity" or
"progressive threat" leaderboard), it needs one of: per-90 normalization, restricting
to attacking-half or forward-pass actions only, or a possession-adjusted alternative
(e.g. only counting the added value of the FIRST N meaningful actions per possession,
or weighting by pass risk/completion probability). That is a real design decision,
not a quick fix -- deliberately left open rather than picking one silently.

Not yet a package (xt_core/): per PLAN_new_models.md's own recommendation, this
stays a prototype until someone decides the leaderboard-shape question above and
signs off on committing to a shape worth wiring into build_players.py/the dashboards.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GRID_X, GRID_Y = 16, 12  # zones across / up the pitch (WhoScored 0-100 coords)


def zone_of(x, y):
    x = min(max(x, 0.0), 99.999)
    y = min(max(y, 0.0), 99.999)
    cx = int(x / 100.0 * GRID_X)
    cy = int(y / 100.0 * GRID_Y)
    return cy * GRID_X + cx


def zone_center(z):
    cy, cx = divmod(z, GRID_X)
    return (cx + 0.5) * 100.0 / GRID_X, (cy + 0.5) * 100.0 / GRID_Y


SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}


def is_shootout(ev):
    p = ev.get("period", {})
    if isinstance(p, dict):
        return p.get("value") == 5 or "Shoot" in (p.get("displayName") or "")
    return "Shoot" in str(p or "")


def carry_end(events, i, ev):
    """Same next-on-ball-touch-within-7s inference build_players.py's progressive-
    carry detection already uses (WhoScored gives no end coordinate for a TakeOn)."""
    pid = ev.get("playerId")
    t0 = (ev.get("minute") or 0) * 60 + (ev.get("second") or 0)
    for nxt in events[i + 1:]:
        if ((nxt.get("minute") or 0) * 60 + (nxt.get("second") or 0)) - t0 > 7:
            break
        if nxt.get("playerId") == pid and nxt.get("x") is not None:
            return nxt.get("x"), nxt.get("y") or 0
    return None, None


def collect_counts(match_dir):
    n_zones = GRID_X * GRID_Y
    shots = [0] * n_zones
    goals = [0] * n_zones
    moves = [0] * n_zones
    trans = [[0] * n_zones for _ in range(n_zones)]
    n_matches = n_shots = n_moves = 0

    for f in sorted(glob.glob(os.path.join(match_dir, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        events = d.get("events") or []
        if not events:
            continue
        n_matches += 1
        for i, ev in enumerate(events):
            t = ev.get("type", {})
            tname = t.get("displayName") if isinstance(t, dict) else None
            if is_shootout(ev):
                continue
            quals = {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}
            if "OwnGoal" in quals:
                continue
            x, y = ev.get("x"), ev.get("y")
            if x is None or y is None:
                continue
            z0 = zone_of(x, y)

            if tname in SHOT_TYPES:
                shots[z0] += 1
                n_shots += 1
                if tname == "Goal":
                    goals[z0] += 1
                continue

            ok = ev.get("outcomeType", {}).get("displayName") == "Successful"
            if not ok:
                continue
            if tname == "Pass":
                ex, ey = ev.get("endX", x), ev.get("endY", y)
            elif tname == "TakeOn":
                ex, ey = carry_end(events, i, ev)
                if ex is None:
                    continue
            else:
                continue
            z1 = zone_of(ex, ey)
            moves[z0] += 1
            trans[z0][z1] += 1
            n_moves += 1

    print(f"{n_matches} matches, {n_shots} shots, {n_moves} completed moves (passes+carries)")
    return shots, goals, moves, trans


def solve_xt(shots, goals, moves, trans, iters=24):
    n_zones = len(shots)
    shot_prob, goal_prob, move_prob = [0.0] * n_zones, [0.0] * n_zones, [0.0] * n_zones
    trans_prob = [[0.0] * n_zones for _ in range(n_zones)]
    for z in range(n_zones):
        total = shots[z] + moves[z]
        if total:
            shot_prob[z] = shots[z] / total
            move_prob[z] = moves[z] / total
        if shots[z]:
            goal_prob[z] = goals[z] / shots[z]
        if moves[z]:
            trans_prob[z] = [c / moves[z] for c in trans[z]]

    xt = [0.0] * n_zones
    for _ in range(iters):
        new_xt = [0.0] * n_zones
        for z in range(n_zones):
            shot_val = shot_prob[z] * goal_prob[z]
            move_val = move_prob[z] * sum(trans_prob[z][z2] * xt[z2] for z2 in range(n_zones))
            new_xt[z] = shot_val + move_val
        xt = new_xt
    return xt, shot_prob, goal_prob, move_prob


def print_grid(values, label, fmt="{:.3f}"):
    print(f"\n{label} (rows = pitch width top->bottom, cols = own goal -> opponent goal)")
    for row in range(GRID_Y - 1, -1, -1):
        cells = [values[row * GRID_X + col] for col in range(GRID_X)]
        print(" ".join(fmt.format(v) for v in cells))


def player_xt_added(match_dir, xt):
    """playerId -> season xT-added, for a quick sanity leaderboard."""
    totals = {}
    names = {}
    for f in sorted(glob.glob(os.path.join(match_dir, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        events = d.get("events") or []
        pid_name = d.get("playerIdNameDictionary", {})
        for i, ev in enumerate(events):
            t = ev.get("type", {})
            tname = t.get("displayName") if isinstance(t, dict) else None
            if tname not in ("Pass", "TakeOn") or is_shootout(ev):
                continue
            if ev.get("outcomeType", {}).get("displayName") != "Successful":
                continue
            pid = ev.get("playerId")
            if pid is None:
                continue
            x, y = ev.get("x"), ev.get("y")
            if x is None or y is None:
                continue
            if tname == "Pass":
                ex, ey = ev.get("endX", x), ev.get("endY", y)
            else:
                ex, ey = carry_end(events, i, ev)
                if ex is None:
                    continue
            added = xt[zone_of(ex, ey)] - xt[zone_of(x, y)]
            totals[pid] = totals.get(pid, 0.0) + added
            if pid not in names:
                names[pid] = pid_name.get(str(pid), str(pid))
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    return [(names[pid], round(v, 2)) for pid, v in ranked[:15]]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", default="2025-26")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    match_dir = os.path.join(root, "..", "laliga", "matches", args.season)
    print(f"xT prototype -- La Liga {args.season} ({GRID_X}x{GRID_Y} grid)\n")

    shots, goals, moves, trans = collect_counts(match_dir)
    xt, shot_prob, goal_prob, move_prob = solve_xt(shots, goals, moves, trans)

    print_grid(xt, "xT value surface")
    print(f"\nmin={min(xt):.4f}  max={max(xt):.4f}  (own-goal-line zone -> centre-forward zone)")

    # Sanity check 1: monotonic-ish increase toward goal along the central channel.
    central_row = GRID_Y // 2
    central = [xt[central_row * GRID_X + col] for col in range(GRID_X)]
    increases = sum(1 for a, b in zip(central, central[1:]) if b >= a)
    print(f"\nCentral-channel monotonicity: {increases}/{GRID_X - 1} steps non-decreasing toward goal "
          f"({'looks sane' if increases >= GRID_X - 3 else 'CHECK THIS'})")

    print("\nTop 15 players by season xT-added (sanity leaderboard):")
    for name, v in player_xt_added(match_dir, xt):
        print(f"  {name:28s} {v:+.2f}")


if __name__ == "__main__":
    main()
