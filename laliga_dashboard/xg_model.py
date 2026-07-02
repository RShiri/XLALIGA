"""Pure-python shot extraction + xG model.

Copied verbatim from laliga/renderer.py (the same model that draws the PNG shot
maps) so the website's xG values match the rendered infographics exactly. Kept
dependency-free (no matplotlib/pandas) so the data builders stay fast.

If the renderer's _estimate_xg / _extract_qualifiers ever change, mirror them here.
"""
import math
import unicodedata

SCALE_Y = 0.80
SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}

# Unified, data-driven xG model — one logistic regression fit on ALL La Liga +
# World Cup shots (11,830 non-penalty shots, 1,166 goals) by
# tools/fit_unified_xg.py. Brier 0.071, log-loss 0.253; summed xG tracks actual
# goals per competition. This SAME model (identical coefficients) is deployed in
# XLALIGA, the World Cup dashboard and BCN so all three report consistent xG.
# Features: distance & the angle the goal-mouth subtends, header, WhoScored
# big-chance flag, and shot situation (free kick / corner / set piece / fast break
# vs open play). Mirror any change in renderer._estimate_xg. Re-fit with the tool.
_INTERCEPT = -3.379503
_COEF = {
    "dist": -0.004175, "angle": 1.421131, "header": -0.580616, "big": 1.891534,
    "freekick": 0.278088, "corner": -0.303916, "setpiece": -0.345961, "fastbreak": 0.455797,
}
# Per-league finishing calibration, added to the logit so summed xG == goals for
# this competition (La Liga finishing; World Cup uses +0.162084). Fit on this
# league's own shot outcomes by the tool.
_CAL_SHIFT = -0.044712
_PENALTY_XG = 0.76


def _shot_angle(x_sb, y_sb):
    """Angle (radians) the goal mouth subtends from the shot location; posts at
    (120, 36) and (120, 44) in StatsBomb coords. Bigger angle = better chance."""
    a = math.hypot(120.0 - x_sb, 36.0 - y_sb)
    b = math.hypot(120.0 - x_sb, 44.0 - y_sb)
    if a <= 0.0 or b <= 0.0:
        return math.pi
    c = max(-1.0, min(1.0, (a * a + b * b - 64.0) / (2.0 * a * b)))
    return math.acos(c)


def is_shootout(ev):
    """True for penalty-SHOOTOUT events (WhoScored period 5 / "PenaltyShootout").

    A shootout decides a drawn knockout tie but its kicks are NOT match shots: they
    must be excluded from xG, shot counts, shot maps, the goals timeline and player
    stats, or a 1-1 tie balloons to ~6 xG and ~9 "goals". The match score stays the
    post-extra-time result; the shootout is reported separately as a penalty score.
    Extra-time shots (periods 3/4) ARE real and stay in."""
    p = ev.get("period", {})
    if isinstance(p, dict):
        return p.get("value") == 5 or "Shoot" in (p.get("displayName") or "")
    return "Shoot" in str(p or "")


def ws_to_sb_x(ws_x):
    if ws_x <= 50:
        return ws_x * (60.0 / 50.0)
    elif ws_x <= 89:
        return 60.0 + (ws_x - 50) * (48.0 / 39.0)
    else:
        return 108.0 + (ws_x - 89) * (12.0 / 11.0)


def estimate_xg(x_sb, y_sb, is_penalty, is_big_chance, body_part, situation="Open Play"):
    """Calibrated xG via the unified logistic model (see _INTERCEPT/_COEF).
    Penalties are fixed at _PENALTY_XG. Coords in StatsBomb metres."""
    if is_penalty:
        return _PENALTY_XG
    dx = 120.0 - x_sb
    dy = 40.0 - y_sb
    dist = max(math.hypot(dx, dy), 0.5)
    z = _INTERCEPT + _CAL_SHIFT
    z += _COEF["dist"] * dist + _COEF["angle"] * _shot_angle(x_sb, y_sb)
    if body_part == "Header":
        z += _COEF["header"]
    if is_big_chance:
        z += _COEF["big"]
    z += {"Free Kick": _COEF["freekick"], "Corner": _COEF["corner"],
          "Set Piece": _COEF["setpiece"], "Fast Break": _COEF["fastbreak"]}.get(situation, 0.0)
    xg = 1.0 / (1.0 + math.exp(-z))
    return round(min(max(xg, 0.01), 0.95), 3)


def ascii_name(name):
    return unicodedata.normalize("NFKD", name or "").encode("ASCII", "ignore").decode("ASCII").strip()


def player_full_name(match_data, player_id):
    for side in ("home", "away"):
        for p in match_data.get(side, {}).get("players", []):
            if p.get("playerId") == player_id:
                return ascii_name(p.get("name", str(player_id)))
    return str(player_id) if player_id is not None else "—"


def extract_qualifiers(ev):
    qual_list = ev.get("qualifiers", [])
    quals = {q.get("type", {}).get("displayName", "") for q in qual_list}
    body = ("Right Foot" if "RightFoot" in quals else
            "Left Foot" if "LeftFoot" in quals else
            "Header" if "Head" in quals else "Unknown")
    situation = ("Penalty" if "Penalty" in quals else
                 "Free Kick" if "DirectFreekick" in quals else
                 "Fast Break" if "FastBreak" in quals else
                 "Set Piece" if "SetPiece" in quals else
                 "Corner" if "FromCorner" in quals else "Open Play")
    if any(z in quals for z in ("SmallBoxCentre", "SmallBoxLeft", "SmallBoxRight",
                                "DeepBoxCentre", "DeepBoxLeft", "DeepBoxRight")):
        zone = "6-Yard Box"
    elif any(z in quals for z in ("BoxCentre", "BoxLeft", "BoxRight")):
        zone = "Inside Box"
    elif any(z in quals for z in ("OutOfBoxCentre", "OutOfBoxLeft", "OutOfBoxRight")):
        zone = "Outside Box"
    else:
        zone = "Unknown"
    big_chance = "BigChance" in quals
    return body, situation, zone, big_chance, quals


def shot_xg(ev):
    """Return (xg, meta) for a single shot event using the renderer's model."""
    x_sb = ws_to_sb_x(ev.get("x", 0))
    y_sb = 80 - ev.get("y", 0) * SCALE_Y
    body, situation, zone, big_chance, quals = extract_qualifiers(ev)
    is_penalty = situation == "Penalty"
    if is_penalty:
        x_sb, y_sb = 108.0, 40.0
    xg = estimate_xg(x_sb, y_sb, is_penalty, big_chance, body, situation)
    return xg, dict(body=body, situation=situation, zone=zone,
                    big_chance=big_chance, penalty=is_penalty)


def player_xa_from_events(match_data):
    """playerId -> summed xA (expected assists).

    Each non-penalty shot's xG is credited to the team-mate who set it up. WhoScored
    tags that player on the shot event as ``relatedPlayerId`` (the same field that
    names the assister on a goal), so xA is just "the xG of the shots you created" —
    the shot-quality analogue of assists. Penalties (unassisted) and shootout kicks
    are excluded."""
    out = {}
    for ev in match_data.get("events", []):
        t = ev.get("type", {})
        if not isinstance(t, dict) or t.get("displayName") not in SHOT_TYPES:
            continue
        if is_shootout(ev):
            continue
        assister = ev.get("relatedPlayerId")
        if assister is None:
            continue
        xg, meta = shot_xg(ev)
        if meta["penalty"]:
            continue  # penalties aren't assisted
        out[assister] = out.get(assister, 0.0) + xg
    return out


def team_xg_from_events(match_data):
    """Sum shot xG per side from WhoScored events. Returns (home_xg, away_xg) or
    (None, None) when there are no shot events to work with."""
    events = match_data.get("events") or []
    home_id = match_data.get("home", {}).get("teamId")
    away_id = match_data.get("away", {}).get("teamId")
    totals = {home_id: 0.0, away_id: 0.0}
    n = 0
    for ev in events:
        tname = ev.get("type", {})
        if not isinstance(tname, dict) or tname.get("displayName") not in SHOT_TYPES:
            continue
        if is_shootout(ev):
            continue  # penalty-shootout kicks are not match shots
        tid = ev.get("teamId")
        if tid not in totals:
            continue
        xg, _ = shot_xg(ev)
        totals[tid] += xg
        n += 1
    if n == 0:
        return None, None
    return round(totals[home_id], 2), round(totals[away_id], 2)
