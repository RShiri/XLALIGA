#!/usr/bin/env python3
"""Regenerate the shipped derived data (matches_detail, shots.js, data.js,
players.js) IN PLACE from the already-shipped matches_detail/*.js.

Why this exists: the raw WhoScored match JSONs are git-ignored and not present in
this clone, so the canonical builders (build_*.py, which read raw events) can't run
here. This tool re-derives the shipped snapshot from the derived files instead, so
the two model changes land on the live site now:

  1. xG calibration — every shot's xG is Platt-scaled with xg_model._calibrate so
     summed xG tracks actual goals (raw geometry over-counts ~1.34x).
  2. xA (expected assists) — each key/assist pass is credited the calibrated xG of
     the shot it created (matched by receiver + time, since the derived files don't
     carry the raw relatedPlayerId the canonical builder uses).

The next time the raw builders run (a re-scrape on the dev machine), they produce
the exact same fields authoritatively and overwrite this snapshot. Keep the
calibration + xA definitions here in sync with xg_model.py.
"""
import glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.dirname(HERE)
sys.path.insert(0, DASH)
from xg_model import _calibrate  # single source of truth for the calibration

DETAIL_DIR = os.path.join(DASH, "matches_detail")
SEASON = "2025-26"
ASSIST_WINDOW = 25  # seconds between a key pass and the shot it created


def _read_wrapped(path, var):
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


def _cal_shot(sit, xg):
    """Calibrated xG for a shot; penalties keep their fixed value."""
    return round(0.76 if sit == "Penalty" else _calibrate(xg), 3)


def _t_sec(o):
    return (o.get("min") or 0) * 60 + (o.get("sec") or 0)


def _match_xa(detail):
    """Return {(player_name, team_name): xa} for one match, by linking every
    key/assist pass to the calibrated xG of the shot its receiver took."""
    home, away = detail["home"]["name"], detail["away"]["name"]
    team_name = {"home": home, "away": away}
    shots = detail["shots"]
    used = [False] * len(shots)
    out = {}
    # earliest unused shot by the pass receiver, just after the pass, same team
    kps = [p for p in detail["passes"] if (p.get("key") or p.get("assist")) and p.get("recv")]
    kps.sort(key=_t_sec)
    for p in kps:
        pt = _t_sec(p)
        best, best_dt = None, None
        for i, s in enumerate(shots):
            if used[i] or s["team"] != p["team"] or s.get("player") != p["recv"]:
                continue
            dt = _t_sec(s) - pt
            if dt < 0 or dt > ASSIST_WINDOW:
                continue
            if best is None or dt < best_dt:
                best, best_dt = i, dt
        if best is None:
            continue
        used[best] = True
        s = shots[best]
        xg = _cal_shot(s.get("sit"), s.get("xg") or 0.0)
        key = (p["player"], team_name[p["team"]])
        out[key] = round(out.get(key, 0.0) + xg, 4)
    return out


def main():
    # Idempotency guard: this rewrites matches_detail IN PLACE with calibrated xG,
    # so a second run would calibrate already-calibrated values. Refuse unless forced.
    sentinel = os.path.join(DETAIL_DIR, ".calibrated")
    if os.path.exists(sentinel) and "--force" not in sys.argv:
        print("Already calibrated (matches_detail/.calibrated exists). "
              "Re-run the raw builders for a clean rebuild, or pass --force.")
        return
    details = {}
    for f in sorted(glob.glob(os.path.join(DETAIL_DIR, "*.js"))):
        if os.path.basename(f).startswith("_"):
            continue
        mid = os.path.basename(f)[:-3]
        details[mid] = _read_wrapped(f, "MATCH_DETAIL")

    # --- 1. recalibrate shots + compute per-player xg/xa; rewrite matches_detail ---
    team_match_xg = {}            # mid -> {"home": xg, "away": xg}
    player_xg = {}                # (name, team) -> calibrated shot xG
    player_xa = {}                # (name, team) -> xA
    for mid, d in details.items():
        home, away = d["home"]["name"], d["away"]["name"]
        team_name = {"home": home, "away": away}
        side_xg = {"home": 0.0, "away": 0.0}
        for s in d["shots"]:
            s["xg"] = _cal_shot(s.get("sit"), s.get("xg") or 0.0)
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

    # --- 2. shots.js (rebuild from calibrated details) ---
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

    # --- 3. data.js (update per-match xg only, preserve everything else) ---
    data_path = os.path.join(DASH, "data.js")
    data = _read_wrapped(data_path, "LL_DATA")
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

    # --- 4. players.js (update xg + add xa/xg_diff/xa_diff/xgi) ---
    players_path = os.path.join(DASH, "players.js")
    pdata = _read_wrapped(players_path, "LL_PLAYERS")
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
    print(f"calibrated xG total: {tot_xg:.1f}  vs goals {tot_goals}  (ratio {tot_xg/tot_goals:.3f})")
    print(f"xA total           : {tot_xa:.1f}  ({tot_xa/tot_xg*100:.0f}% of shots assisted-weighted)")
    open(sentinel, "w").write("matches_detail xG calibrated + xA added by regen_from_details.py\n")


def player_xg_in_match(d, side, name):
    return sum(s["xg"] for s in d["shots"] if s["team"] == side and s.get("player") == name)


if __name__ == "__main__":
    main()
