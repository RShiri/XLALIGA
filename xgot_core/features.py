# -*- coding: utf-8 -*-
"""Shot feature extraction for the placement-based xGOT model.

Reuses xg_core_v3's pre-shot context features (the 23-feature v3 shape) verbatim
via import — placement (goal-mouth landing spot) is new signal ON TOP of that,
not a replacement, per PLAN_new_models.md item 1: placement alone underfits
(a keeper's positioning interacts with shot type/angle/big-chance).

Restricted to shots that reached the goal frame: Goal + SavedShot — the exact
"on target" definition the dashboards already use (see
laliga_dashboard/build_match_details.py's `onTarget` field). ShotOnPost is NOT
on target by that definition (it hit the frame, was never heading at the keeper).
"""
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xg_core_v3.features import (  # noqa: E402
    shot_angle, ws_to_sb, is_shootout, extract_qualifiers, _assist_pass,
    _extra_features, _qual_set, _fnum, base_feature_dict,
    FEATURE_NAMES as PRESHOT_FEATURES, MONOTONE as PRESHOT_MONOTONE,
)

ON_TARGET_TYPES = {"Goal", "SavedShot"}

# Goal frame geometry in WhoScored's GoalMouthY/Z units — matches
# laliga_dashboard/match.js's GM helper, which plots the same on-target shot map:
# posts at Y=45.2/54.8 (width 9.6, centred on 50), crossbar at Z=38 (0=ground).
POST_L, POST_R, CROSSBAR = 45.2, 54.8, 38.0
GOAL_CENTER_Y = (POST_L + POST_R) / 2.0

_PLACEMENT = ["plc_gy_abs", "plc_gy_post_dist", "plc_gz", "plc_gz_frac",
              "plc_top_corner", "plc_low_corner", "plc_central"]
FEATURE_NAMES = list(PRESHOT_FEATURES) + _PLACEMENT
# Placement -> save-probability is NOT monotonic (both extremes of height are hard
# to save, mid-height is the keeper's easiest zone) -- left unconstrained so the
# GBM can find that shape; only the inherited pre-shot priors stay constrained.
MONOTONE = dict(PRESHOT_MONOTONE)


def placement_features(gy, gz):
    gy = _fnum(gy, GOAL_CENTER_Y)
    gz = max(0.0, min(CROSSBAR, _fnum(gz, 0.0)))
    gy_abs = abs(gy - GOAL_CENTER_Y)
    gy_post_dist = min(abs(gy - POST_L), abs(gy - POST_R))
    gz_frac = gz / CROSSBAR
    return {
        "plc_gy_abs": gy_abs,
        "plc_gy_post_dist": gy_post_dist,
        "plc_gz": gz,
        "plc_gz_frac": gz_frac,
        "plc_top_corner": 1.0 if (gy_post_dist < 1.5 and gz_frac > 0.5) else 0.0,
        "plc_low_corner": 1.0 if (gy_post_dist < 1.5 and gz_frac < 0.3) else 0.0,
        "plc_central": 1.0 if gy_abs < 1.5 else 0.0,
    }


def goal_mouth_coords(ev):
    """(GoalMouthY, GoalMouthZ) qualifier values for a shot event, or (None, None)
    if either is absent (not every shot carries them)."""
    gy = gz = None
    for q in ev.get("qualifiers", []):
        dn = q.get("type", {}).get("displayName")
        if dn == "GoalMouthY":
            gy = _fnum(q.get("value"), None)
        elif dn == "GoalMouthZ":
            gz = _fnum(q.get("value"), None)
    return gy, gz


def ascii_name(name):
    return unicodedata.normalize("NFKD", name or "").encode("ASCII", "ignore").decode("ASCII").strip()


def norm_surname(name):
    """Loose cross-provider player match key (same spirit as the dashboards'
    _norm_player): ascii-fold, lowercase, keep only the surname (last token)."""
    a = ascii_name(name).lower().strip()
    parts = [p for p in a.replace(".", " ").split() if p]
    return parts[-1] if parts else a


def fotmob_xgot_buckets(match_data):
    """(side, normalized surname, minute) -> [xgot, ...] from FotMob's own shotmap,
    for market anchoring (mirrors build_match_details.py's cross-provider matching:
    no shared shot id exists, so team+surname+minute is the best-effort key)."""
    out = {}
    for s in match_data.get("_fotmob_shots") or []:
        xgot = s.get("xgot")
        if xgot is None:
            continue
        key = (s.get("team"), norm_surname(s.get("player")), s.get("min", 0))
        out.setdefault(key, []).append(xgot)
    return out


def pop_market(buckets, side, player, minute):
    key = (side, norm_surname(player), minute)
    vals = buckets.get(key)
    if not vals:
        for m in (minute - 1, minute + 1):
            vals = buckets.get((side, norm_surname(player), m))
            if vals:
                break
    return vals.pop(0) if vals else None


def iter_ontarget_shots(match_data, league="", match_id=""):
    """Yield one training row per on-target shot (Goal/SavedShot, non-shootout,
    non-own-goal, with placement coords present): the full pre-shot feature set +
    placement features + label + a market_xgot anchor (FotMob's own number for the
    same real shot, when it has one)."""
    home = match_data.get("home", {})
    away = match_data.get("away", {})
    side_of = {home.get("teamId"): "home", away.get("teamId"): "away"}
    pid_name = match_data.get("playerIdNameDictionary", {})
    evs = match_data.get("events", [])
    byid = {e.get("eventId"): e for e in evs}
    fm_buckets = fotmob_xgot_buckets(match_data)
    prev_pass = None
    for ev in evs:
        t = ev.get("type", {})
        dn = t.get("displayName") if isinstance(t, dict) else None
        if dn == "Pass":
            prev_pass = ev
        if not isinstance(t, dict) or dn not in ON_TARGET_TYPES:
            continue
        if is_shootout(ev):
            continue
        quals = _qual_set(ev)
        if "OwnGoal" in quals:
            continue
        gy, gz = goal_mouth_coords(ev)
        if gy is None or gz is None:
            continue  # not every shot carries goal-mouth qualifiers -- skip, don't guess

        body, situation, big = extract_qualifiers(ev)
        is_pen = situation == "Penalty"
        if is_pen:
            continue  # penalties are ~76% conversion regardless of placement noise;
                       # xg_core's own penalty_xg constant already covers them
        x_sb, y_sb = ws_to_sb(ev.get("x", 0), ev.get("y", 0))
        ap = _assist_pass(ev, byid, prev_pass)
        feats = base_feature_dict(x_sb, y_sb, body, situation, big,
                                  assisted=ev.get("relatedPlayerId") is not None)
        feats.update(_extra_features(quals, ap))
        feats.update(placement_features(gy, gz))

        side = side_of.get(ev.get("teamId"))
        minute = ev.get("minute", 0)
        player_name = pid_name.get(str(ev.get("playerId")), "")
        market_xgot = pop_market(fm_buckets, side, player_name, minute)

        row = dict(feats)
        row.update(
            league=league, match_id=str(match_id), event_id=ev.get("eventId"),
            team_id=ev.get("teamId"), player_id=ev.get("playerId"), minute=minute,
            situation=situation, body_part=body, side=side,
            market_xgot=market_xgot, y=1 if dn == "Goal" else 0,
        )
        yield row
