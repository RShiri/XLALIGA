"""Shot extraction + xG/xA scoring, routed through the shared models.

xG comes from <repo root>/xg_core_v3: the 23-feature v3 artifact (LR + monotone-GBM
+ market-distill blend + isotonic map, retrained on La Liga + EPL 4 seasons each +
WC, ~77k shots). Five of its features come from the shot's assisting pass, so shots
are scored a whole match at a time via match_xg_by_event(match_data) and read back
per shot — NOT from isolated coordinates (that path silently degrades). xA comes from
xg_core's pass-level artifact (P(pass becomes an assist), two-stage + isotonic).
Scoring is pure python (stdlib); with lightgbm installed both upgrade to the full
blends — every path is calibrated (Sum xG ~= goals, Sum xA ~= assists per season).

Public surface the builders import is unchanged except shot_xg(ev, xg_by_event) now
takes the per-match lookup. renderer.build_shot_df scores through the same v3 engine,
so the site and the PNGs still agree. The legacy scalar estimate_xg() (v2 xg_core)
survives only for the offline tools/. Retrain xG with xg_core_v3's training CLI and
xA with xg_core/train_xa.py.
"""
import math
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xg_core.score import XGScorer            # legacy v2 scalar xG (offline tools only)
from xg_core.xa_score import XAScorer
from xg_core_v3 import XGScorer as XGScorerV3  # v3 23-feature xG (assist-context aware)
from xg_core_v3.features import (SHOT_TYPES as _V3_SHOT_TYPES,   # mirror iter_match_xg's
                                 is_shootout as _v3_is_shootout,  # exact shot filter, so
                                 _qual_set as _v3_qual_set)       # we score the same set

_LEAGUE = "LaLiga"           # per-league calibration shift inside the artifacts
_XG = XGScorer()             # scalar estimate_xg() — kept for tools/, NOT the live path
_XG_V3 = XGScorerV3()        # the live engine: scores a whole match, keyed by id(event)
_XA = XAScorer()

SCALE_Y = 0.80
SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}


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


def estimate_xg(x_sb, y_sb, is_penalty, is_big_chance, body_part,
                situation="Open Play", assisted=False):
    """LEGACY scalar xG via the v2 xg_core artifact — kept only for the offline
    tools/ scripts. The live pipeline scores shots through match_xg_by_event()
    below: the v3 model needs the assisting pass, which a scalar can't supply
    (its 9 assist-context features would silently default to 0). Do NOT route
    shipped data through this. Coords in StatsBomb metres."""
    return _XG.estimate_xg(x_sb, y_sb, is_penalty, is_big_chance, body_part,
                           situation, assisted=assisted, league=_LEAGUE)


def match_xg_by_event(match_data):
    """id(event) -> calibrated v3 xG for every real shot in the match.

    Score ONCE per match, then look each shot up by object identity via shot_xg().
    The v3 model derives 5 of its 23 features from the shot's assisting pass, so it
    must see the whole match's events — scoring from isolated coordinates silently
    zeroes those features and returns degraded xG.

    KEY IS id(event), NOT WhoScored eventId. eventId is never unique across a match's
    events, and in ~15% of La Liga games two *shots* even share one; a dict keyed on
    eventId (e.g. dict(iter_match_xg(...))) silently drops or mis-assigns those shots.
    Every caller passes the same event objects from this match_data to shot_xg(), so
    id() matches and is unique. Penalties are valued inside (~0.79); own goals and
    penalty-shootout kicks are excluded. Single source of shot xG for site AND PNGs."""
    evs = match_data.get("events", [])
    byid = {e.get("eventId"): e for e in evs}   # for relatedEventId -> assist pass
    prev_pass = None
    out = {}
    for ev in evs:
        t = ev.get("type", {})
        dn = t.get("displayName") if isinstance(t, dict) else None
        if dn == "Pass":
            prev_pass = ev
        if not isinstance(t, dict) or dn not in _V3_SHOT_TYPES:
            continue
        if _v3_is_shootout(ev) or "OwnGoal" in _v3_qual_set(ev):
            continue
        out[id(ev)] = _XG_V3.xg_from_shot_event(ev, byid, prev_pass, league=_LEAGUE)
    return out


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


def shot_xg(ev, xg_by_event):
    """Return (xg, meta) for one shot event. `xg_by_event` is the per-match lookup
    from match_xg_by_event(match_data): v3 xG is scored once with the whole match in
    hand (so the assisting-pass features are populated), then read back here by the
    event's object identity (id(ev)) — NOT eventId, which collides. A shot absent
    from the map (own goal / penalty-shootout kick) scores 0.0. `meta`
    (body/situation/zone/big_chance/penalty) is derived from the event's own
    qualifiers, unchanged."""
    body, situation, zone, big_chance, quals = extract_qualifiers(ev)
    is_penalty = situation == "Penalty"
    xg = xg_by_event.get(id(ev), 0.0)   # object identity — see match_xg_by_event
    return xg, dict(body=body, situation=situation, zone=zone,
                    big_chance=big_chance, penalty=is_penalty)


def player_xa_from_events(match_data):
    """playerId -> summed xA (expected assists), from the pass-level xA model.

    xA(pass) = calibrated P(this successful pass becomes a goal assist), summed
    over every successful pass a player attempts — so a killer ball the striker
    wastes still earns credit, and no shot is required. Sums are calibrated so
    league-wide xA == actual assists. (The old version credited the passer with
    the xG of the shot that followed, which required a shot and ran ~11% hot.)"""
    return _XA.player_xa_from_events(match_data, league=_LEAGUE)


def team_xg_from_events(match_data):
    """Sum shot xG per side from WhoScored events. Returns (home_xg, away_xg) or
    (None, None) when there are no shot events to work with."""
    events = match_data.get("events") or []
    home_id = match_data.get("home", {}).get("teamId")
    away_id = match_data.get("away", {}).get("teamId")
    xg_by_event = match_xg_by_event(match_data)   # score the whole match once
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
        xg, _ = shot_xg(ev, xg_by_event)
        totals[tid] += xg
        n += 1
    if n == 0:
        return None, None
    return round(totals[home_id], 2), round(totals[away_id], 2)
