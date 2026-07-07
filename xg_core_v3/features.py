# -*- coding: utf-8 -*-
"""Shot feature extraction for the 23-feature v3 xG model (stdlib-only, deployable).

Self-contained: this is the runtime "single source of truth" for geometry AND the
v3 shot-context extras. It matches the training extraction (XG V3\\features_v3.py)
value-for-value. Vendor this + score.py + xg_artifact.json into a build pipeline.

Base-14 geometry is identical to the shipped xg_core; the 9 extras are:
  first_touch, one_on_one, volley, individual_play          (from the shot's own tags)
  from_cross, from_throughball, from_cutback, from_layoff, pass_length_n
                                                           (from the LINKED assist pass)
Outcome fields (goal-mouth placement, blocked coords, miss direction) are never used.
"""
import math

SCALE_Y = 0.80
SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}

_BASE = [
    "dist", "log_dist", "angle", "dist_x_angle", "header", "other_body",
    "header_x_dist", "big_chance", "big_x_dist", "freekick", "corner",
    "setpiece", "fastbreak", "assisted",
]
_EXTRA = [
    "first_touch", "one_on_one", "volley", "individual_play",
    "from_cross", "from_throughball", "from_cutback", "from_layoff", "pass_length_n",
]
FEATURE_NAMES = _BASE + _EXTRA          # order MUST match xg_artifact.json feature_names
MONOTONE = {"dist": -1, "log_dist": -1, "angle": 1, "big_chance": 1}
_ZERO_EXTRA = {k: 0.0 for k in _EXTRA}


# ------------------------------------------------------------------ geometry
def shot_angle(x_sb, y_sb):
    a = math.hypot(120.0 - x_sb, 36.0 - y_sb)
    b = math.hypot(120.0 - x_sb, 44.0 - y_sb)
    if a <= 0.0 or b <= 0.0:
        return math.pi
    c = max(-1.0, min(1.0, (a * a + b * b - 64.0) / (2.0 * a * b)))
    return math.acos(c)


def ws_to_sb_x(ws_x):
    if ws_x <= 50:
        return ws_x * (60.0 / 50.0)
    elif ws_x <= 89:
        return 60.0 + (ws_x - 50) * (48.0 / 39.0)
    else:
        return 108.0 + (ws_x - 89) * (12.0 / 11.0)


def ws_to_sb(ws_x, ws_y):
    return ws_to_sb_x(ws_x), 80.0 - ws_y * SCALE_Y


def is_shootout(ev):
    p = ev.get("period", {})
    if isinstance(p, dict):
        return p.get("value") == 5 or "Shoot" in (p.get("displayName") or "")
    return "Shoot" in str(p or "")


# ---------------------------------------------------------------- qualifiers
def _qual_set(ev):
    return {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}


def _qual_map(ev):
    out = {}
    for q in ev.get("qualifiers", []):
        out[q.get("type", {}).get("displayName", "")] = q.get("value")
    return out


def _fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def extract_qualifiers(ev):
    quals = _qual_set(ev)
    body = ("Right Foot" if "RightFoot" in quals else
            "Left Foot" if "LeftFoot" in quals else
            "Header" if "Head" in quals else "Unknown")
    situation = ("Penalty" if "Penalty" in quals else
                 "Free Kick" if "DirectFreekick" in quals else
                 "Fast Break" if "FastBreak" in quals else
                 "Set Piece" if "SetPiece" in quals else
                 "Corner" if "FromCorner" in quals else "Open Play")
    return body, situation, "BigChance" in quals


def _assist_pass(ev, byid, prev_pass):
    """The pass that created this shot (linked via relatedEventId, else the last
    teammate pass immediately before it), or None."""
    r = ev.get("relatedEventId")
    if r is not None:
        pe = byid.get(r)
        if pe and pe.get("type", {}).get("displayName") == "Pass" \
                and pe.get("teamId") == ev.get("teamId"):
            return pe
    if prev_pass is not None and prev_pass.get("teamId") == ev.get("teamId"):
        return prev_pass
    return None


def _extra_features(shot_quals, ap):
    cross = tb = cut = lay = 0.0
    plen = 0.0
    if ap is not None:
        pset = _qual_set(ap)
        pmap = _qual_map(ap)
        cross = 1.0 if "Cross" in pset else 0.0
        tb = 1.0 if "Throughball" in pset else 0.0
        lay = 1.0 if "LayOff" in pset else 0.0
        plen = _fnum(pmap.get("Length"))
        sx = _fnum(ap.get("x"))
        pey = _fnum(pmap.get("PassEndY"), 50.0)
        if cross and sx >= 83.0 and abs(pey - 50.0) <= 22.0 and "Chipped" not in pset:
            cut = 1.0
    return {
        "first_touch": 1.0 if "FirstTouch" in shot_quals else 0.0,
        "one_on_one": 1.0 if "OneOnOne" in shot_quals else 0.0,
        "volley": 1.0 if "Volley" in shot_quals else 0.0,
        "individual_play": 1.0 if "IndividualPlay" in shot_quals else 0.0,
        "from_cross": cross, "from_throughball": tb, "from_cutback": cut,
        "from_layoff": lay, "pass_length_n": min(plen, 60.0) / 60.0,
    }


# ------------------------------------------------------------ feature dicts
def base_feature_dict(x_sb, y_sb, body_part, situation, big_chance, assisted=False):
    dist = max(math.hypot(120.0 - x_sb, 40.0 - y_sb), 0.5)
    ang = shot_angle(x_sb, y_sb)
    header = 1.0 if body_part == "Header" else 0.0
    other = 1.0 if body_part == "Unknown" else 0.0
    big = 1.0 if big_chance else 0.0
    return {
        "dist": dist, "log_dist": math.log1p(dist), "angle": ang,
        "dist_x_angle": dist * ang, "header": header, "other_body": other,
        "header_x_dist": header * dist, "big_chance": big, "big_x_dist": big * dist,
        "freekick": 1.0 if situation == "Free Kick" else 0.0,
        "corner": 1.0 if situation == "Corner" else 0.0,
        "setpiece": 1.0 if situation == "Set Piece" else 0.0,
        "fastbreak": 1.0 if situation == "Fast Break" else 0.0,
        "assisted": 1.0 if assisted else 0.0,
    }


def feature_dict(x_sb, y_sb, body_part, situation, big_chance, assisted=False, extras=None):
    """23-key feature dict. `extras` (dict of the 9 v3 extras) defaults to zeros — so
    the scalar path stays callable, but full v3 xG needs shot_feature_dict() below,
    which computes the extras from the event + its assisting pass."""
    d = base_feature_dict(x_sb, y_sb, body_part, situation, big_chance, assisted)
    d.update(_ZERO_EXTRA)
    if extras:
        d.update(extras)
    return d


def shot_feature_dict(ev, byid, prev_pass):
    """Full 23-feature dict for one shot event.
    Returns (feats, is_penalty, body_part, situation)."""
    quals = _qual_set(ev)
    body, situation, big = extract_qualifiers(ev)
    is_pen = situation == "Penalty"
    x_sb, y_sb = ws_to_sb(ev.get("x", 0), ev.get("y", 0))
    if is_pen:
        x_sb, y_sb = 108.0, 40.0
    ap = None if is_pen else _assist_pass(ev, byid, prev_pass)
    feats = base_feature_dict(x_sb, y_sb, body, situation, big,
                              assisted=ev.get("relatedPlayerId") is not None)
    feats.update(_extra_features(quals, ap))
    return feats, is_pen, body, situation


def iter_shots(match_data, league="", match_id=""):
    """23-feature training/parity iterator (matches XG V3\\features_v3.iter_shots_v3)."""
    evs = match_data.get("events", [])
    byid = {e.get("eventId"): e for e in evs}
    prev_pass = None
    for ev in evs:
        t = ev.get("type", {})
        dn = t.get("displayName") if isinstance(t, dict) else None
        if dn == "Pass":
            prev_pass = ev
        if not isinstance(t, dict) or dn not in SHOT_TYPES:
            continue
        if is_shootout(ev):
            continue
        quals = _qual_set(ev)
        if "OwnGoal" in quals:
            continue
        feats, is_pen, body, situation = shot_feature_dict(ev, byid, prev_pass)
        row = dict(feats)
        row.update(
            league=league, match_id=str(match_id), event_id=ev.get("eventId"),
            team_id=ev.get("teamId"), player_id=ev.get("playerId"),
            minute=ev.get("minute"), penalty=is_pen, situation=situation,
            body_part=body, y=1 if dn == "Goal" else 0,
        )
        yield row
