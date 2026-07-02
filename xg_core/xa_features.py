"""Pass feature extraction for the xA model: WhoScored events -> feature rows.

stdlib-only on purpose, like features.py — this module is vendored into the
dashboards' build pipelines. Geometry is shared with the xG model (StatsBomb
coords, posts at (120,36)/(120,44)) via features.shot_angle / ws_to_sb.

What counts as a pass here: every WhoScored "Pass" event with outcome
Successful (an unsuccessful pass cannot assist — it scores 0 by definition,
so it is never modelled). Throw-ins, corners, free kicks and goal kicks ARE
passes in the WhoScored stream and stay in with type flags: set-piece
deliveries produce real assists and the flags let the model price them.

Labels the trainer needs, all derived from the raw event stream:
* shot_followed — the pass carries KeyPass / ShotAssist (a shot came next).
* xg_target     — the v2 xG of that shot, resolved through the shot's
                  relatedEventId -> this pass's eventId (same team). NaN when
                  the link cannot be resolved; the stage-B fit masks NaNs.
* y (assist)    — the pass carries IntentionalGoalAssist: the Opta assist
                  definition, and exactly what the dashboards count in "a".
                  Deliberately NOT "linked shot was a goal" (812 vs 673): that
                  variant includes deflected scrambles Opta refuses to credit,
                  and calibrating on it would inflate xA ~20% above the assist
                  totals shown next to it.
"""
import math

from .features import shot_angle, ws_to_sb, is_shootout

# Stable feature order — artifact, boosters and the pure-python scorer all
# index by this list. Append only; never reorder.
PASS_FEATURE_NAMES = [
    "end_dist",          # metres from the pass END to goal centre
    "end_log_dist",      # log(1+end_dist)
    "end_angle",         # goal-mouth angle at the pass end (receiver's view)
    "end_dist_x_angle",  # interaction, mirrors the shot model
    "start_dist",        # metres from the pass ORIGIN to goal centre
    "delta_dist",        # start_dist - end_dist: metres of progress toward goal
    "length",            # pass length in SB metres
    "into_box",          # ends inside the penalty area
    "into_six",          # ends inside the six-yard box
    "cross",             # Cross qualifier
    "throughball",       # Throughball qualifier (the classic assist weapon)
    "cutback",           # derived: from the byline pulled back across the box
    "chipped",           # Chipped
    "longball",          # Longball
    "layoff",            # LayOff (little touch to a shooter)
    "headpass",          # HeadPass (flick-ons)
    "corner",            # CornerTaken (in-swinger deliveries)
    "freekick",          # FreekickTaken / IndirectFreekickTaken delivery
    "throwin",           # ThrowIn
]

# Same discipline as the shot model: the GBM may never learn that ending a
# pass further from goal, or at a narrower angle, makes an assist MORE likely.
PASS_MONOTONE = {"end_dist": -1, "end_log_dist": -1, "end_angle": 1}


def pass_feature_dict(x_sb, y_sb, ex_sb, ey_sb, quals):
    """The single source of truth for turning one successful pass into model
    features. quals is the set of qualifier displayNames on the event."""
    end_dist = max(math.hypot(120.0 - ex_sb, 40.0 - ey_sb), 0.5)
    start_dist = max(math.hypot(120.0 - x_sb, 40.0 - y_sb), 0.5)
    ang = shot_angle(ex_sb, ey_sb)
    length = math.hypot(ex_sb - x_sb, ey_sb - y_sb)
    # cutback: won the byline wide, pulled it back into the central box —
    # the highest-value pass in football and invisible to every type qualifier
    cutback = (x_sb >= 105.0 and (y_sb <= 30.0 or y_sb >= 50.0)
               and 30.0 <= ey_sb <= 50.0 and ex_sb <= x_sb)
    return {
        "end_dist": end_dist,
        "end_log_dist": math.log1p(end_dist),
        "end_angle": ang,
        "end_dist_x_angle": end_dist * ang,
        "start_dist": start_dist,
        "delta_dist": start_dist - end_dist,
        "length": length,
        "into_box": 1.0 if (ex_sb >= 102.0 and 18.0 <= ey_sb <= 62.0) else 0.0,
        "into_six": 1.0 if (ex_sb >= 114.0 and 30.0 <= ey_sb <= 50.0) else 0.0,
        "cross": 1.0 if "Cross" in quals else 0.0,
        "throughball": 1.0 if "Throughball" in quals else 0.0,
        "cutback": 1.0 if cutback else 0.0,
        "chipped": 1.0 if "Chipped" in quals else 0.0,
        "longball": 1.0 if "Longball" in quals else 0.0,
        "layoff": 1.0 if "LayOff" in quals else 0.0,
        "headpass": 1.0 if "HeadPass" in quals else 0.0,
        "corner": 1.0 if "CornerTaken" in quals else 0.0,
        "freekick": 1.0 if ("FreekickTaken" in quals
                            or "IndirectFreekickTaken" in quals) else 0.0,
        "throwin": 1.0 if "ThrowIn" in quals else 0.0,
    }


def quals_of(ev):
    return {q.get("type", {}).get("displayName", "")
            for q in ev.get("qualifiers", [])}


def iter_passes(match_data, league="", match_id="", xg_scorer=None):
    """Yield one row dict per successful pass in a matchCentreData blob.

    Rows carry PASS_FEATURE_NAMES plus identifiers, the stage-A label
    ``shot_followed``, the stage-B target ``xg_target`` (v2 xG of the linked
    shot when xg_scorer is given and the relatedEventId chain resolves, else
    NaN) and the final label ``y`` (IntentionalGoalAssist).
    """
    from .features import SHOT_TYPES, extract_qualifiers

    events = match_data.get("events") or []
    # shots indexed by (teamId, relatedEventId) -> shot event, to resolve the
    # pass -> shot link for stage B. eventId is only unique per team.
    shot_for_pass = {}
    for ev in events:
        t = ev.get("type", {})
        if not isinstance(t, dict) or t.get("displayName") not in SHOT_TYPES:
            continue
        if is_shootout(ev):
            continue
        rel = ev.get("relatedEventId")
        if rel is not None:
            shot_for_pass[(ev.get("teamId"), rel)] = ev

    for ev in events:
        t = ev.get("type", {})
        if not isinstance(t, dict) or t.get("displayName") != "Pass":
            continue
        if is_shootout(ev):
            continue
        if (ev.get("outcomeType", {}) or {}).get("displayName") != "Successful":
            continue
        quals = quals_of(ev)
        x_sb, y_sb = ws_to_sb(ev.get("x", 0.0), ev.get("y", 0.0))
        ex_sb, ey_sb = ws_to_sb(ev.get("endX", ev.get("x", 0.0)),
                                ev.get("endY", ev.get("y", 0.0)))
        row = pass_feature_dict(x_sb, y_sb, ex_sb, ey_sb, quals)

        shot_followed = ("KeyPass" in quals or "ShotAssist" in quals
                         or "IntentionalGoalAssist" in quals)
        xg_target = float("nan")
        if shot_followed and xg_scorer is not None:
            shot = shot_for_pass.get((ev.get("teamId"), ev.get("eventId")))
            if shot is not None:
                body, situation, big = extract_qualifiers(shot)
                if situation != "Penalty":
                    sx, sy = ws_to_sb(shot.get("x", 0.0), shot.get("y", 0.0))
                    xg_target = xg_scorer.estimate_xg(
                        sx, sy, False, big, body, situation,
                        assisted=True, league=league or None)

        row.update(
            league=league, match_id=str(match_id),
            event_id=ev.get("eventId"), team_id=ev.get("teamId"),
            player_id=ev.get("playerId"), minute=ev.get("minute"),
            shot_followed=1 if shot_followed else 0,
            xg_target=xg_target,
            y=1 if "IntentionalGoalAssist" in quals else 0,
        )
        yield row
