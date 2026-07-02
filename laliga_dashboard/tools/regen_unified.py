#!/usr/bin/env python3
"""Regenerate the shipped derived data (matches_detail, shots.js, data.js,
players.js) IN PLACE from the already-shipped matches_detail/*.js, using the
current unified xG model.

Unlike the older regen_from_details.py (which Platt-recalibrated the *stored* xG,
and so was not safe to re-run), this recomputes every shot's xG from its stored
FEATURES (x, y WhoScored coords, situation, body, big-chance) via
xg_model.estimate_xg. Because the features never change, it is idempotent — re-run
it any time the model coefficients change.

Why it exists: the raw WhoScored match JSONs (laliga/matches/) are git-ignored and
absent in this clone, so the canonical builders (build_*.py, which read raw events)
can't run here. matches_detail keeps all the model inputs, so we re-derive from it.
The next raw-builder run on the dev machine produces identical fields.

  xA (expected assists): each key/assist pass is credited the calibrated xG of the
  shot its receiver took (matched by receiver + time), since the derived files don't
  carry the raw relatedPlayerId the canonical builder uses.
"""
import glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.dirname(HERE)
sys.path.insert(0, DASH)
from xg_model import estimate_xg, ws_to_sb_x, SCALE_Y  # single source of truth

DETAIL_DIR = os.path.join(DASH, "matches_detail")
SEASON = "2025-26"
ASSIST_WINDOW = 25  # seconds between a key pass and the shot it created


def _read_wrapped(path):
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"=\s*(\{.*\}|\[.*\])\s*;?\s*$", txt, re.S)
    if not m:
        raise ValueError(f"cannot parse {path}")
    return json.loads(m.group(1))


def _write_wrapped(path, prefix, obj, pretty=False):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(prefix)
        if pretty:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        else:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")


def _shot_model_xg(s):
    """Recompute a shot's xG from its stored features with the unified model."""
    sit = s.get("sit") or "Open Play"
    body = s.get("body") or "Unknown"
    big = bool(s.get("big"))
    if sit == "Penalty":
        return round(estimate_xg(108.0, 40.0, True, big, body, sit), 3)
    x_sb = ws_to_sb_x(s.get("x", 0) or 0)
    y_sb = 80.0 - (s.get("y", 0) or 0) * SCALE_Y
    return round(estimate_xg(x_sb, y_sb, False, big, body, sit), 3)


def _t_sec(o):
    return (o.get("min") or 0) * 60 + (o.get("sec") or 0)


def _match_xa(detail):
    """Return {(player_name, team_name): xa} for one match, by linking every
    key/assist pass to the (already recomputed) xG of the shot its receiver took."""
    home, away = detail["home"]["name"], detail["away"]["name"]
    team_name = {"home": home, "away": away}
    shots = detail["shots"]
    used = [False] * len(shots)
    out = {}
    kps = [p for p in detail["passes"] if (p.get("key") or p.get("assist")) and p.get("recv")]
    kps.sort(key=_t_sec)
    for p in kps:
        pt = _t_sec(p)
        best, best_dt = None, None
        for i, s in enumerate(shots):
            if used[i] or s["team"] != p["team"] or s.get("player") != p["recv"]:
                continue
            if s.get("sit") == "Penalty":
                continue  # penalties aren't assisted
            dt = _t_sec(s) - pt
            if dt < 0 or dt > ASSIST_WINDOW:
                continue
            if best is None or dt < best_dt:
                best, best_dt = i, dt
        if best is None:
            continue
        used[best] = True
        key = (p["player"], team_name[p["team"]])
        out[key] = round(out.get(key, 0.0) + shots[best]["xg"], 4)
    return out


def player_xg_in_match(d, side, name):
    return sum(s["xg"] for s in d["shots"] if s["team"] == side and s.get("player") == name)


def main():
    details = {}
    for f in sorted(glob.glob(os.path.join(DETAIL_DIR, "*.js"))):
        if os.path.basename(f).startswith("_"):
            continue
        mid = os.path.basename(f)[:-3]
        details[mid] = _read_wrapped(f)

    team_match_xg = {}            # mid -> {"home": xg, "away": xg}
    player_xg = {}                # (name, team) -> shot xG
    player_xa = {}                # (name, team) -> xA
    for mid, d in details.items():
        home, away = d["home"]["name"], d["away"]["name"]
        team_name = {"home": home, "away": away}
        side_xg = {"home": 0.0, "away": 0.0}
        for s in d["shots"]:
            s["xg"] = _shot_model_xg(s)   # recompute from features
            side_xg[s["team"]] += s["xg"]
            if s.get("player"):
                k = (s["player"], team_name[s["team"]])
                player_xg[k] = player_xg.get(k, 0.0) + s["xg"]
        team_match_xg[mid] = {k: round(v, 2) for k, v in side_xg.items()}
        xa = _match_xa(d)
        for k, v in xa.items():
            player_xa[k] = player_xa.get(k, 0.0) + v
        # fold per-player xg/xa into the line-up cards
        for side in ("home", "away"):
            for grp in ("starters", "subs"):
                for e in d["lineups"][side][grp]:
                    k = (e["name"], team_name[side])
                    e["xg"] = round(player_xg_in_match(d, side, e["name"]), 2)
                    e["xa"] = round(xa.get(k, 0.0), 2)
        _write_wrapped(os.path.join(DETAIL_DIR, mid + ".js"),
                       "window.MATCH_DETAIL = ", d)

    # --- shots.js ---
    shots_out = []
    for mid, d in details.items():
        tn = {"home": d["home"]["name"], "away": d["away"]["name"]}
        for s in d["shots"]:
            opp = tn["away" if s["team"] == "home" else "home"]
            shots_out.append({
                "t": tn[s["team"]], "o": opp, "h": s["team"] == "home",
                "x": s["x"], "y": s["y"], "xg": s["xg"], "g": bool(s.get("goal")),
                "ot": bool(s.get("onTarget")), "s": s.get("sit"), "m": s.get("min"),
            })
    _write_wrapped(os.path.join(DASH, "shots.js"), "window.LL_SHOTS = ",
                   {SEASON: shots_out})

    # --- data.js (update per-match xg only) ---
    data_path = os.path.join(DASH, "data.js")
    data = _read_wrapped(data_path)
    updated = 0
    for m in data["seasons"][SEASON].get("matches", []):
        tx = team_match_xg.get(str(m.get("id")))
        if not tx:
            continue
        m["xg_home"], m["xg_away"] = tx["home"], tx["away"]
        if isinstance(m.get("stats"), dict):
            m["stats"]["xg"] = [tx["home"], tx["away"]]
        updated += 1
    _write_wrapped(data_path,
                   "// AUTO-GENERATED by build_data.py — do not edit by hand.\nwindow.LL_DATA = ",
                   data, pretty=True)

    # --- players.js (xg + xa/xg_diff/xa_diff/xgi) ---
    players_path = os.path.join(DASH, "players.js")
    pdata = _read_wrapped(players_path)
    for p in pdata.get(SEASON, []):
        k = (p["name"], p["team"])
        p["xg"] = round(player_xg.get(k, 0.0), 2)
        p["xa"] = round(player_xa.get(k, 0.0), 2)
        p["xg_diff"] = round((p.get("g") or 0) - p["xg"], 2)
        p["xa_diff"] = round((p.get("a") or 0) - p["xa"], 2)
        p["xgi"] = round(p["xg"] + p["xa"], 2)
    _write_wrapped(players_path, "window.LL_PLAYERS = ", pdata)

    tot_goals = sum(1 for d in details.values() for s in d["shots"] if s.get("goal"))
    tot_xg = sum(s["xg"] for d in details.values() for s in d["shots"])
    tot_xa = sum(player_xa.values())
    print(f"details rewritten : {len(details)}")
    print(f"data.js matches   : {updated} updated")
    print(f"players updated    : {len(pdata.get(SEASON, []))}")
    print(f"xG total          : {tot_xg:.1f}  vs goals {tot_goals}  (ratio {tot_xg/tot_goals:.3f})")
    print(f"xA total          : {tot_xa:.1f}  ({tot_xa/tot_xg*100:.0f}% of shot xG is assisted)")


if __name__ == "__main__":
    main()
