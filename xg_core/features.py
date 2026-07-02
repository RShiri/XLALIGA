"""Shot feature extraction: WhoScored matchCentreData events -> model feature rows.

stdlib-only on purpose: this module is vendored (together with score.py) into the
dashboards' build pipelines, which must stay dependency-free. Geometry matches
laliga_dashboard/xg_model.py exactly (StatsBomb coords, posts at (120,36)/(120,44)).
"""
import math

SCALE_Y = 0.80
SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}

# Stable feature order — the artifact, the LightGBM boosters and the pure-python
# scorer all index by this list. Append only; never reorder.
FEATURE_NAMES = [
    "dist",          # metres to goal centre
    "log_dist",      # log(1+dist): lets linear models bend the distance decay
    "angle",         # radians the goal mouth subtends (visibility of the goal)
    "dist_x_angle",  # interaction: a wide angle up close != wide angle far out
    "header",        # 1 if headed
    "other_body",    # 1 if body part unknown/other (chest, knee)
    "header_x_dist", # headers decay faster with distance than foot shots
    "big_chance",    # WhoScored BigChance tag (proxy for pressure/GK position)
    "big_x_dist",    # stops the big-chance bonus applying full-strength far out
    "freekick",      # direct free kick
    "corner",        # shot from a corner phase
    "setpiece",      # indirect set-piece phase
    "fastbreak",     # counter-attack
    "assisted",      # a teammate's pass created the shot (relatedPlayerId set)
]

# Monotonicity we enforce on the GBM so it can't learn upward-sloping pockets
# that inflate bad shots: further = never better, wider angle = never worse.
MONOTONE = {"dist": -1, "log_dist": -1, "angle": 1, "big_chance": 1}


def shot_angle(x_sb, y_sb):
    """Angle (radians) the goal mouth subtends from the shot location."""
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
    """Penalty-shootout kicks are not match shots — always exclude."""
    p = ev.get("period", {})
    if isinstance(p, dict):
        return p.get("value") == 5 or "Shoot" in (p.get("displayName") or "")
    return "Shoot" in str(p or "")


def extract_qualifiers(ev):
    quals = {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}
    body = ("Right Foot" if "RightFoot" in quals else
            "Left Foot" if "LeftFoot" in quals else
            "Header" if "Head" in quals else "Unknown")
    situation = ("Penalty" if "Penalty" in quals else
                 "Free Kick" if "DirectFreekick" in quals else
                 "Fast Break" if "FastBreak" in quals else
                 "Set Piece" if "SetPiece" in quals else
                 "Corner" if "FromCorner" in quals else "Open Play")
    return body, situation, "BigChance" in quals


def feature_dict(x_sb, y_sb, body_part, situation, big_chance, assisted=False):
    """The single source of truth for turning shot context into model features.
    Training (features -> DataFrame) and runtime scoring (score.py) both call this."""
    dist = max(math.hypot(120.0 - x_sb, 40.0 - y_sb), 0.5)
    ang = shot_angle(x_sb, y_sb)
    header = 1.0 if body_part == "Header" else 0.0
    other = 1.0 if body_part == "Unknown" else 0.0
    big = 1.0 if big_chance else 0.0
    return {
        "dist": dist,
        "log_dist": math.log1p(dist),
        "angle": ang,
        "dist_x_angle": dist * ang,
        "header": header,
        "other_body": other,
        "header_x_dist": header * dist,
        "big_chance": big,
        "big_x_dist": big * dist,
        "freekick": 1.0 if situation == "Free Kick" else 0.0,
        "corner": 1.0 if situation == "Corner" else 0.0,
        "setpiece": 1.0 if situation == "Set Piece" else 0.0,
        "fastbreak": 1.0 if situation == "Fast Break" else 0.0,
        "assisted": 1.0 if assisted else 0.0,
    }


def iter_shots(match_data, league="", match_id=""):
    """Yield one row dict per real shot in a WhoScored matchCentreData blob.

    Rows carry the model features plus identifiers (league, match_id, event_id,
    team_id, player_id), the label ``y`` and a ``penalty`` flag so the trainer can
    split penalties out (they are scored as a constant, never modelled).
    """
    for ev in match_data.get("events", []):
        t = ev.get("type", {})
        if not isinstance(t, dict) or t.get("displayName") not in SHOT_TYPES:
            continue
        if is_shootout(ev):
            continue
        quals = {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}
        if "OwnGoal" in quals:
            # own goals carry no xG (market standard) and would enter training as
            # "goals" at junk coordinates — a label bug that inflates the long tail
            continue
        body, situation, big = extract_qualifiers(ev)
        is_pen = situation == "Penalty"
        x_sb, y_sb = ws_to_sb(ev.get("x", 0), ev.get("y", 0))
        if is_pen:
            x_sb, y_sb = 108.0, 40.0
        row = feature_dict(x_sb, y_sb, body, situation, big,
                           assisted=ev.get("relatedPlayerId") is not None)
        row.update(
            league=league, match_id=str(match_id),
            event_id=ev.get("eventId"), team_id=ev.get("teamId"),
            player_id=ev.get("playerId"), minute=ev.get("minute"),
            penalty=is_pen, situation=situation, body_part=body,
            y=1 if t.get("displayName") == "Goal" else 0,
        )
        yield row
