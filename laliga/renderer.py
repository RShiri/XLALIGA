"""
FIFA World Cup 2026 – Match Analytics Dashboard Renderer
Mirrors the rendering architecture of BCNPROJECT webup (generate_all_assets.py,
shotmap_whoscored.py) while producing a white-canvas, 3-column infographic
sized for X/Twitter high-resolution image posts.

Layout (24" × 14" canvas, 200 DPI ≈ 4800 × 2800 px):
  Row 0 (header):  full-width match info, team badges, score, logos
  Row 1 (mid):     [Team A Pass Network | Central Stats Table | Team B Pass Network]
  Row 2 (bottom):  [Team A Shot Map    | Final Third Entries | Team B Shot Map   ]
"""

from __future__ import annotations

import os
import math
import json
import logging
import sys
import unicodedata
from pathlib import Path
from typing import Optional

# Use Agg backend for headless server rendering (must precede pyplot import)
import matplotlib
matplotlib.use("Agg")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from matplotlib.patches import FancyBboxPatch
from matplotlib.lines import Line2D
from mplsoccer import Pitch, VerticalPitch

from laliga.team_colors import get_team_colors

# ── Coordinate + shot helpers (inlined — renderer is self-contained) ────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
SCALE_Y = 0.80

# xG via the shared xg_core v2 artifact — the SAME scorer xg_model.py routes
# through, so the PNG infographics and the website report identical xG.
# (Replaces the hard-coded unified-LR coefficients; retrain with xg_core/train.py.)
sys.path.insert(0, str(_REPO_ROOT))
from xg_core.score import XGScorer            # legacy v2 scalar — kept for _estimate_xg
from xg_core_v3 import XGScorer as XGScorerV3  # v3 23-feature xG — the live shot path
from xg_core_v3.features import (SHOT_TYPES as _V3_SHOT_TYPES,   # mirror the dashboard's
                                 is_shootout as _v3_is_shootout,  # match_xg_by_event so
                                 _qual_set as _v3_qual_set)       # PNG == site exactly

_XG_SCORER = XGScorer()
_XG_SCORER_V3 = XGScorerV3()   # scores a whole match at once (needs the assisting pass)
_XG_LEAGUE = "LaLiga"


def _match_xg_by_objid(match_data: dict) -> dict:
    """id(event) -> v3 xG for each real shot, mirroring laliga_dashboard/xg_model.
    match_xg_by_event so the PNG and the site report identical xG. Keyed by object
    identity, NOT WhoScored eventId: eventId is not unique within a match (it collides
    in ~15% of games), so a dict keyed on it silently drops/mis-assigns shots."""
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
        out[id(ev)] = _XG_SCORER_V3.xg_from_shot_event(ev, byid, prev_pass, league=_XG_LEAGUE)
    return out


def _ws_to_sb_x(ws_x: float) -> float:
    if ws_x <= 50:   return ws_x * (60.0 / 50.0)
    elif ws_x <= 89: return 60.0 + (ws_x - 50) * (48.0 / 39.0)
    else:            return 108.0 + (ws_x - 89) * (12.0 / 11.0)


def _estimate_xg(x_sb: float, y_sb: float, is_penalty: bool, is_big_chance: bool,
                 body_part: str, situation: str = "Open Play",
                 assisted: bool = False) -> float:
    """LEGACY scalar xG (v2). build_shot_df scores shots through the v3 event path
    (_XG_SCORER_V3.iter_match_xg) so the PNG matches the site; this remains only for
    any caller that has bare coordinates and no match context."""
    return _XG_SCORER.estimate_xg(x_sb, y_sb, is_penalty, is_big_chance,
                                  body_part, situation, assisted=assisted,
                                  league=_XG_LEAGUE)


def _ascii_name(name: str) -> str:
    """Transliterate accented / special characters to their ASCII base letters."""
    return unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("ASCII").strip()


def _player_name(match_data: dict, player_id) -> str:
    for side in ("home", "away"):
        for p in match_data.get(side, {}).get("players", []):
            if p.get("playerId") == player_id:
                name = _ascii_name(p.get("name", ""))
                parts = name.split()
                return parts[-1] if len(parts) >= 2 else name
    return str(player_id)


def _player_full_name(match_data: dict, player_id) -> str:
    for side in ("home", "away"):
        for p in match_data.get(side, {}).get("players", []):
            if p.get("playerId") == player_id:
                return _ascii_name(p.get("name", str(player_id)))
    return str(player_id)


def _team_id_for_name(match_data: dict, team_name: str) -> int:
    for side in ("home", "away"):
        info = match_data.get(side, {})
        if team_name.lower() in info.get("name", "").lower():
            return info["teamId"]
    raise ValueError(f"Team '{team_name}' not found in match data")


def _extract_qualifiers(ev: dict):
    qual_list = ev.get("qualifiers", [])
    quals = {q.get("type", {}).get("displayName", "") for q in qual_list}
    body = ("Right Foot" if "RightFoot" in quals else
            "Left Foot"  if "LeftFoot"  in quals else
            "Header"     if "Head"      in quals else "Unknown")
    situation = ("Penalty"    if "Penalty"         in quals else
                 "Free Kick"  if "DirectFreekick"  in quals else
                 "Fast Break" if "FastBreak"       in quals else
                 "Set Piece"  if "SetPiece"        in quals else
                 "Corner"     if "FromCorner"      in quals else "Open Play")
    if any(z in quals for z in ("SmallBoxCentre", "SmallBoxLeft", "SmallBoxRight",
                                 "DeepBoxCentre",  "DeepBoxLeft",  "DeepBoxRight")):
        zone = "6-Yard Box"
    elif any(z in quals for z in ("BoxCentre", "BoxLeft", "BoxRight")):
        zone = "Inside Box"
    elif any(z in quals for z in ("OutOfBoxCentre", "OutOfBoxLeft", "OutOfBoxRight")):
        zone = "Outside Box"
    else:
        zone = "Unknown"
    big_chance = "BigChance" in quals
    one_on_one = "OneOnOne"  in quals
    gm_y = gm_z = None
    for q in qual_list:
        qname = q.get("type", {}).get("displayName", "")
        try:
            if qname == "GoalMouthY":   gm_y = float(q.get("value", 0))
            elif qname == "GoalMouthZ": gm_z = float(q.get("value", 0))
        except (TypeError, ValueError):
            pass
    return body, situation, zone, big_chance, one_on_one, gm_y, gm_z


_SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}


def build_shot_df(match_data: dict, team_name: str) -> pd.DataFrame:
    tid = _team_id_for_name(match_data, team_name)
    # Score every shot in the match once (v3 xG reads each shot's assisting pass) and
    # look up per shot by object identity — the SAME path the dashboard uses, PNG==site.
    xg_by_event = _match_xg_by_objid(match_data)
    rows = []
    for ev in match_data.get("events", []):
        if ev.get("teamId") != tid:
            continue
        type_name = ev.get("type", {}).get("displayName", "")
        if type_name not in _SHOT_TYPES:
            continue
        # Own goals belong to the opponent and are not a shot by this team; the raw
        # event sits at this team's own-goal end, which would plot a bogus "goal" dot
        # at the wrong end of the shot map. Skip them.
        _q = {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}
        if ev.get("isOwnGoal") or "OwnGoal" in _q:
            continue
        # Penalty-shootout kicks (period 5) decide a drawn knockout tie but are NOT match
        # shots — excluding them keeps xG/shot map real (a 1-1 that goes to penalties would
        # otherwise show ~6 xG from a dozen shootout "shots").
        _p = ev.get("period", {})
        if isinstance(_p, dict) and (_p.get("value") == 5 or "Shoot" in (_p.get("displayName") or "")):
            continue
        x_sb = _ws_to_sb_x(ev.get("x", 0))
        y_sb = 80 - ev.get("y", 0) * SCALE_Y
        body, situation, zone, big_chance, one_on_one, gm_y, gm_z = _extract_qualifiers(ev)
        is_penalty = (situation == "Penalty")
        if is_penalty:
            x_sb, y_sb = 108.0, 40.0
        period_raw = ev.get("period", {}).get("displayName", "")
        period = ("ET" if "Extra" in period_raw else
                  "H2" if "Second" in period_raw else "H1")
        xg_stored = ev.get("xG")
        rows.append({
            "x":            x_sb,
            "y":            y_sb,
            "minute":       ev.get("minute", 0),
            "player":       _player_name(match_data, ev.get("playerId")),
            "full_name":    _player_full_name(match_data, ev.get("playerId")),
            "is_goal":      type_name == "Goal",
            "is_on_target": type_name in ("SavedShot", "Goal"),
            "xG":           (xg_stored if xg_stored is not None
                             else xg_by_event.get(id(ev), 0.0)),
            "body_part":    body,
            "situation":    situation,
            "zone":         zone,
            "big_chance":   big_chance,
            "one_on_one":   one_on_one,
            "period":       period,
            "gm_y":         gm_y,
            "gm_z":         gm_z,
        })
    return pd.DataFrame(rows)

# ── Visual constants (white-canvas theme) ──────────────────────────────────
CANVAS_BG       = "#ffffff"
DIVIDER_CLR     = "#D3D3D3"
TEXT_DARK       = "#111111"
TEXT_MID        = "#555555"
TEXT_LIGHT      = "#888888"
PITCH_GREEN     = "#2d572c"   # kept for backwards compat
PITCH_LINE      = "#ffffff"
PITCH_GREEN_LIGHT = "#3a6b38"
PITCH_WHITE     = "#f5f5f5"   # clean white/off-white pitch surface
PITCH_LINE_DARK = "#888888"   # visible lines on white pitch

FONT_MAIN    = "DejaVu Sans"
FONT_BOLD    = "DejaVu Sans"

FIG_W, FIG_H = 30, 17
FIG_DPI      = 200

logging.basicConfig(level=logging.INFO, format="[LALIGA] %(message)s")
log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))  # type: ignore


def _contrasting_text(bg_hex: str) -> str:
    r, g, b = _hex_to_rgb(bg_hex)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#ffffff" if luminance < 0.45 else "#111111"


def _load_logo(team_name: str, size: tuple[int, int] = (80, 80)):
    """Return a PIL Image for the team logo, or None if not found."""
    try:
        from PIL import Image
        logo_dir = _REPO_ROOT / "team_logos" / "laliga"
        for ext in ("png", "jpg", "svg"):
            p = logo_dir / f"{team_name}.{ext}"
            if p.exists():
                img = Image.open(p).convert("RGBA")
                img.thumbnail(size, Image.LANCZOS)
                return img
    except Exception as e:
        log.exception("Error loading logo for %s: %s", team_name, e)
    return None


# ══════════════════════════════════════════════════════════════════════════
# SECTION 1 – HEADER
# ══════════════════════════════════════════════════════════════════════════

def _place_flag(ax: plt.Axes, team_name: str,
                x: float, y_ctr: float,
                h: float = 0.38, align: str = "center") -> None:
    """
    Overlay a flag/badge image on the header axes, preserving aspect ratio.
    Coordinates are in data space (axes fraction).
    align can be "center", "left", or "right".
    """
    try:
        from PIL import Image
        import numpy as _np

        logo_dir = _REPO_ROOT / "team_logos" / "laliga"
        for ext in ("png", "jpg"):
            p = logo_dir / f"{team_name}.{ext}"
            if p.exists():
                img = Image.open(p).convert("RGBA")
                img_w, img_h = img.size
                img_aspect = img_w / img_h

                # Calculate the correct width in axes units to maintain aspect ratio
                fig = ax.get_figure()
                fig_w, fig_h = fig.get_size_inches()
                bbox = ax.get_position()
                W_inches = bbox.width * fig_w
                H_inches = bbox.height * fig_h
                axes_aspect = W_inches / H_inches

                # Compute w to preserve aspect ratio
                w_real = h * img_aspect / axes_aspect

                # Determine x_ctr based on alignment
                if align == "left":
                    x_ctr = x + w_real / 2
                elif align == "right":
                    x_ctr = x - w_real / 2
                else:
                    x_ctr = x

                ax.imshow(
                    _np.array(img),
                    extent=[x_ctr - w_real / 2, x_ctr + w_real / 2,
                            y_ctr - h / 2, y_ctr + h / 2],
                    aspect="auto",
                    zorder=6,
                    interpolation="lanczos",
                )
                return
        log.warning("Flag file not found for team: %s", team_name)
    except Exception as e:
        log.exception("Error placing flag for %s: %s", team_name, e)


def _draw_header(fig: plt.Figure, ax: plt.Axes, match_data: dict) -> None:
    """
    Renders the header:
      Row 1 (top):    Stage label + date/venue
      Row 2 (center): [HOME FLAG | HOME NAME]  [SCORE]  [AWAY NAME | AWAY FLAG]
    The score sits horizontally centered between the two team name blocks.
    """
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_autoscale_on(False)   # imshow must not reset our coord space
    ax.axis("off")
    ax.set_facecolor(CANVAS_BG)

    meta   = match_data.get("meta", {})
    home_d = match_data.get("home", {})
    away_d = match_data.get("away", {})

    home_name  = home_d.get("name", "Home")
    away_name  = away_d.get("name", "Away")
    def _score(team_d):
        scores = team_d.get("scores")
        if isinstance(scores, dict) and scores.get("fulltime") is not None:
            return scores["fulltime"]
        s = team_d.get("score")
        return s if s is not None else 0
    home_score = _score(home_d)
    away_score = _score(away_d)
    home_pk    = home_d.get("penalty_score")
    away_pk    = away_d.get("penalty_score")

    home_colors = get_team_colors(home_name, fallback_home=True)
    away_colors = get_team_colors(away_name, fallback_home=False)

    if home_d.get("primary_color"):
        home_colors["primary"] = home_d["primary_color"]
    if away_d.get("primary_color"):
        away_colors["primary"] = away_d["primary_color"]

    stage       = meta.get("stage", "LaLiga")
    matchday    = meta.get("matchday")
    stage_label = "La Liga" + (f" — Matchday {matchday}" if matchday else (f" — {stage}" if stage else ""))
    date_str    = meta.get("date", "")
    venue       = meta.get("venue", "")
    city = meta.get("city", "")
    country = meta.get("country", "")
    if city and country and city != country:
        city_c = f"{city}, {country}"
    else:
        city_c = city or country

    # ── Title line ────────────────────────────────────────────────────
    ax.text(0.5, 0.96, stage_label,
            ha="center", va="top", fontsize=18, color=TEXT_MID,
            fontfamily=FONT_MAIN, fontweight="normal",
            transform=ax.transAxes)

    venue_line = " | ".join(filter(None, [date_str, venue, city_c]))
    ax.text(0.5, 0.82, venue_line,
            ha="center", va="top", fontsize=14, color=TEXT_LIGHT,
            fontfamily=FONT_MAIN, transform=ax.transAxes)

    # ── Layout constants ──────────────────────────────────────────────
    # Three equal horizontal zones: home [0–0.36] | score [0.36–0.64] | away [0.64–1.0]
    badge_h      = 0.50
    badge_y_bot  = 0.08
    badge_y_top  = badge_y_bot + badge_h
    badge_y_ctr  = badge_y_bot + badge_h / 2  # ≈ 0.33

    home_badge_x  = 0.02
    home_badge_w  = 0.34
    score_x       = 0.50
    away_badge_x  = 0.64
    away_badge_w  = 0.34

    # ── Home Team Section (No colored box) ──────────────────────────────
    # Flag image (left of the name)
    _place_flag(ax, home_name, x=0.12, y_ctr=badge_y_ctr, h=0.38, align="right")

    # Team Name (right of the flag)
    ax.text(0.14, badge_y_ctr,
            home_name, ha="left", va="center",
            fontsize=35, fontweight="bold", color=TEXT_DARK,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=4)

    # ── Away Team Section (No colored box) ──────────────────────────────
    # Team Name (left of the flag)
    ax.text(0.86, badge_y_ctr,
            away_name, ha="right", va="center",
            fontsize=35, fontweight="bold", color=TEXT_DARK,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=4)

    # Flag image (right of the name)
    _place_flag(ax, away_name, x=0.88, y_ctr=badge_y_ctr, h=0.38, align="left")

    # ── Score — centered between badges ───────────────────────────────
    score_txt = f"{home_score}  —  {away_score}"
    ax.text(score_x, badge_y_ctr,
            score_txt,
            ha="center", va="center",
            fontsize=60, fontweight="bold", color=TEXT_DARK,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=4)

    # Penalty sub-score
    if home_pk is not None and away_pk is not None:
        ax.text(score_x, badge_y_bot - 0.03,
                f"({home_pk} – {away_pk} pens)",
                ha="center", va="top", fontsize=18, color=TEXT_MID,
                fontfamily=FONT_MAIN, transform=ax.transAxes)

    # ── Divider ───────────────────────────────────────────────────────
    ax.axhline(0.02, color=DIVIDER_CLR, linewidth=0.8)


# ══════════════════════════════════════════════════════════════════════════
# SECTION 2A – PASS NETWORK (per-team, on subplot axes)
# ══════════════════════════════════════════════════════════════════════════

def _draw_pass_network(ax: plt.Axes, match_data: dict,
                       team_side: str, team_name: str, color_val: str) -> None:
    """
    Mirrors generate_all_assets.generate_passnetwork() but draws onto a
    pre-existing subplot axes (ax) instead of creating a new figure.
    Uses VerticalPitch with white background to match white-canvas theme.
    """
    tid = match_data.get(team_side, {}).get("teamId")
    if not tid:
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                fontsize=13, color=TEXT_MID, transform=ax.transAxes)
        return

    team_block  = match_data.get(team_side, {})
    jersey_map: dict[int, int] = {}
    for player in team_block.get("players", []):
        pid = player.get("playerId")
        jn  = player.get("shirtNo")
        if pid is not None and jn is not None:
            jersey_map[pid] = int(jn)

    events_raw = match_data.get("events", [])
    rows = []
    for ev in events_raw:
        pid = ev.get("playerId")
        if pid is None:
            continue
        rows.append({
            "id":       ev.get("id"),
            "event_id": ev.get("eventId", 0),
            "team_id":  ev.get("teamId"),
            "type":     ev.get("type", {}).get("displayName", ""),
            "player_id": pid,
            "x":        ev.get("x", 0) * 1.2,
            "y":        80 - ev.get("y", 0) * SCALE_Y,
            "minute":   ev.get("minute", 0),
            "second":   ev.get("second", 0),
            "outcome":  ev.get("outcomeType", {}).get("displayName", ""),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        ax.text(0.5, 0.5, "No event data", ha="center", va="center",
                fontsize=13, color=TEXT_MID, transform=ax.transAxes)
        return

    df["newsecond"] = 60 * df["minute"] + df["second"]
    df = df.sort_values(["newsecond", "event_id"]).reset_index(drop=True)

    sub_df      = df.loc[(df["team_id"] == tid) & (df["type"].isin(["SubstitutionOff", "SubstitutionOn"]))]
    first_sub   = sub_df["newsecond"].min()
    if pd.isna(first_sub) or first_sub <= 2700:
        first_sub = 2700  # 45 minutes

    df_pre = df.loc[df["newsecond"] < first_sub].copy()

    recipients = []
    for i in range(len(df_pre)):
        row = df_pre.iloc[i]
        rec = None
        if row["team_id"] == tid and row["type"] == "Pass" and row["outcome"] == "Successful":
            for j in range(i + 1, len(df_pre)):
                nxt = df_pre.iloc[j]
                if nxt["team_id"] != tid:
                    break
                if nxt["outcome"] == "Successful":
                    rec = nxt["player_id"]
                    break
        recipients.append(rec)

    df_pre["recipient"] = recipients
    completions = df_pre.loc[
        (df_pre["team_id"] == tid) &
        (df_pre["type"] == "Pass") &
        (df_pre["outcome"] == "Successful")
    ].dropna(subset=["recipient"]).copy()

    if completions.empty:
        _draw_empty_pass_network(ax, team_name, color_val, "Insufficient pass data")
        return

    suc_actions = df_pre.loc[(df_pre["team_id"] == tid) & (df_pre["outcome"] == "Successful")]
    avg_locs = suc_actions.groupby("player_id").agg({"x": "mean", "y": "mean", "id": "count"})
    avg_locs.columns = ["x", "y", "count"]

    completions["passer"] = completions["player_id"]
    passes_between = (
        completions.groupby(["passer", "recipient"])["id"]
        .count()
        .reset_index()
        .rename(columns={"id": "pass_count"})
    )
    passes_between = passes_between.merge(avg_locs, left_on="passer",    right_index=True)
    passes_between = passes_between.merge(avg_locs, left_on="recipient", right_index=True, suffixes=["", "_end"])
    passes_between = passes_between.loc[passes_between["pass_count"] >= 3]

    # ── Draw pitch on the supplied axes ───────────────────────────────
    pitch = VerticalPitch(pitch_type="statsbomb", pitch_color="#ffffff",
                          line_color="#c7c7c7", linewidth=1.2)
    pitch.draw(ax=ax)
    ax.set_facecolor(CANVAS_BG)

    if not passes_between.empty:
        mn_p = passes_between["pass_count"].min()
        mx_p = passes_between["pass_count"].max()
        rng  = max(mx_p - mn_p, 1)
        MIN_LW, MAX_LW = 1.2, 7.0

        for _, row in passes_between.iterrows():
            lw = MIN_LW + (row["pass_count"] - mn_p) / rng * (MAX_LW - MIN_LW)
            al = 0.35 + (row["pass_count"] - mn_p) / rng * 0.55
            dist  = math.hypot(row["x_end"] - row["x"], row["y_end"] - row["y"])
            angle = math.atan2(row["y_end"] - row["y"], row["x_end"] - row["x"])
            delta = min(4.0, dist * 0.35)
            tx = row["x"] + (dist - delta) * math.cos(angle)
            ty = row["y"] + (dist - delta) * math.sin(angle)
            ax.annotate("",
                xy=(ty, tx), xytext=(row["y"], row["x"]),
                arrowprops=dict(
                    arrowstyle="-|>", linewidth=lw,
                    color=color_val, alpha=al,
                    connectionstyle="arc3,rad=0.12",
                ),
                zorder=1,
            )

    if not avg_locs.empty:
        sizes = 180 + avg_locs["count"] * 22
        pitch.scatter(avg_locs.x, avg_locs.y, s=sizes,
                      color="white", edgecolors=color_val, linewidth=2,
                      alpha=1, ax=ax, zorder=2)

        for pid, row in avg_locs.iterrows():
            jn = jersey_map.get(int(pid), "")
            if not jn:
                pname = _player_name(match_data, pid)
                jn = "".join(n[0] for n in pname.split()[:2]).upper()
            pitch.annotate(str(jn), xy=(row["x"], row["y"]),
                           c=color_val, va="center", ha="center",
                           size=14, weight="bold", ax=ax, zorder=3)

    # Legend for line thickness
    if not passes_between.empty:
        q33 = int(np.percentile(passes_between["pass_count"], 33))
        q67 = int(np.percentile(passes_between["pass_count"], 67))
        lw_handles = [
            Line2D([0], [0], color=color_val, lw=1.5, alpha=0.9,
                   label=f"Low  (≤{q33})"),
            Line2D([0], [0], color=color_val, lw=4.0, alpha=0.9,
                   label=f"Med  ({q33+1}–{q67})"),
            Line2D([0], [0], color=color_val, lw=7.0, alpha=0.9,
                   label=f"High (>{q67})"),
        ]
        leg = ax.legend(handles=lw_handles, loc="lower right",
                        fontsize=10, title="Pass volume",
                        title_fontsize=11, framealpha=0.8,
                        facecolor="#f5f5f5", edgecolor=DIVIDER_CLR)
        leg.get_title().set_color(TEXT_MID)

    ax.set_title(f"{team_name} — Pass Network",
                 fontsize=15, fontweight="bold", color=TEXT_DARK,
                 fontfamily=FONT_BOLD, pad=4)


def _draw_empty_pass_network(ax: plt.Axes, team_name: str,
                              color_val: str, reason: str = "") -> None:
    pitch = VerticalPitch(pitch_type="statsbomb", pitch_color="#ffffff",
                          line_color="#c7c7c7", linewidth=1.2)
    pitch.draw(ax=ax)
    ax.set_facecolor(CANVAS_BG)
    ax.text(0.5, 0.5, reason or "No data",
            ha="center", va="center", fontsize=14, color=TEXT_LIGHT,
            transform=ax.transAxes)
    ax.set_title(f"{team_name} — Pass Network",
                 fontsize=15, fontweight="bold", color=TEXT_DARK, pad=4)


# ══════════════════════════════════════════════════════════════════════════
# SECTION 2B – CENTRAL STATS TABLE
# ══════════════════════════════════════════════════════════════════════════

def _draw_stats_table(ax: plt.Axes, match_data: dict,
                      home_name: str, away_name: str,
                      home_color: str, away_color: str) -> None:
    """
    Minimal 3-column stats table:
      [Team A value] | [label] | [Team B value]
    9 rows as specified in the blueprint.
    """
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor(CANVAS_BG)

    stats = match_data.get("match_stats", {})

    def _stat(key: str, side: str):
        """Accepts both nested {"key": {"home": v}} and flat {"key_home": v} formats."""
        v = stats.get(key)
        if isinstance(v, dict):
            return v.get(side)
        return stats.get(f"{key}_{side}")

    def _v(key: str, side: str, fmt: str = "{}") -> str:
        val = _stat(key, side)
        if val is None:
            return "—"
        return fmt.format(val)

    xg_h = _stat("xg", "home")
    xg_a = _stat("xg", "away")
    # No real (FotMob/Opta) xG? Fall back to the geometric shot-model estimate
    # — the same per-shot xG the shot maps display, so the two stay consistent.
    if xg_h is None:
        df_h = build_shot_df(match_data, home_name)
        if not df_h.empty:
            xg_h = float(df_h["xG"].sum())
    if xg_a is None:
        df_a = build_shot_df(match_data, away_name)
        if not df_a.empty:
            xg_a = float(df_a["xG"].sum())

    bc_created_h = _stat("big_chances_created", "home")
    bc_missed_h  = _stat("big_chances_missed",  "home") or 0
    bc_created_a = _stat("big_chances_created", "away")
    bc_missed_a  = _stat("big_chances_missed",  "away") or 0
    bc_h = f"{bc_created_h} ({bc_missed_h})" if bc_created_h is not None else "—"
    bc_a = f"{bc_created_a} ({bc_missed_a})" if bc_created_a is not None else "—"

    passes_total_h  = _stat("passes_total", "home") or _stat("passes", "home")
    passes_accur_h  = _stat("passes_accurate", "home")
    pass_pct_h      = _stat("passes_accuracy", "home") or _stat("pass_accuracy", "home")
    passes_total_a  = _stat("passes_total", "away") or _stat("passes", "away")
    passes_accur_a  = _stat("passes_accurate", "away")
    pass_pct_a      = _stat("passes_accuracy", "away") or _stat("pass_accuracy", "away")

    # Derive accurate count from total × accuracy if not stored directly
    if passes_accur_h is None and passes_total_h and pass_pct_h:
        passes_accur_h = int(round(passes_total_h * pass_pct_h / 100))
    if passes_accur_a is None and passes_total_a and pass_pct_a:
        passes_accur_a = int(round(passes_total_a * pass_pct_a / 100))

    def _fmt_passes(total, accurate, pct):
        if total is None:
            return "—"
        if accurate is not None and pct is not None:
            return f"{accurate}/{total} ({pct}%)"
        if accurate is not None:
            return f"{accurate}/{total}"
        if pct is not None:
            return f"{total} ({pct}%)"
        return str(total)

    p_h = _fmt_passes(passes_total_h, passes_accur_h, pass_pct_h)
    p_a = _fmt_passes(passes_total_a, passes_accur_a, pass_pct_a)

    rows: list[tuple[str, str, str]] = [
        (f"{xg_h:.2f}" if xg_h is not None else "—",  "xG",                   f"{xg_a:.2f}" if xg_a is not None else "—"),
        (_v("possession", "home", "{}%"),               "Possession",            _v("possession", "away", "{}%")),
        (_v("shots_on_target", "home"),                 "Shots on Target",       _v("shots_on_target", "away")),
        (_v("shots", "home"),                           "Shots",                 _v("shots", "away")),
        (bc_h,                                          "Big Chances (Missed)",  bc_a),
        (p_h,                                           "Passes (Accuracy)",     p_a),
        (_v("duels_won", "home", "{}%"),                "Duels Won",             _v("duels_won", "away", "{}%")),
        (_v("saves", "home"),                           "Saves",                 _v("saves", "away")),
        (_v("fouls", "home"),                           "Fouls",                 _v("fouls", "away")),
    ]

    # Drop any stat with no data on either side (e.g. xG when only WhoScored data exists)
    rows = [r for r in rows if not (r[0] == "—" and r[2] == "—")]

    # Column header with team names
    ax.text(0.18, 0.97, home_name, ha="center", va="top",
            fontsize=14, fontweight="bold", color=home_color,
            fontfamily=FONT_BOLD, transform=ax.transAxes)
    ax.text(0.82, 0.97, away_name, ha="center", va="top",
            fontsize=14, fontweight="bold", color=away_color,
            fontfamily=FONT_BOLD, transform=ax.transAxes)

    # Vertical dividers
    for xv in (0.36, 0.64):
        ax.axvline(xv, color=DIVIDER_CLR, linewidth=0.9, ymin=0.02, ymax=0.93)

    # Title divider
    ax.axhline(0.91, color=DIVIDER_CLR, linewidth=0.8)

    # Title
    ax.text(0.5, 0.945, "M A T C H   S T A T I S T I C S",
            ha="center", va="center", fontsize=13,
            color=TEXT_MID, fontfamily=FONT_MAIN,
            fontweight="bold", transform=ax.transAxes)

    n_rows = len(rows)
    if n_rows == 0:          # no stats to show — skip the table rather than /0
        return
    row_h  = 0.86 / n_rows  # vertical space per row
    for i, (lv, label, rv) in enumerate(rows):
        y_center = 0.89 - (i + 0.5) * row_h

        # Zebra stripe
        if i % 2 == 0:
            bg = FancyBboxPatch((0.01, y_center - row_h * 0.45), 0.98, row_h * 0.9,
                                boxstyle="round,pad=0.005",
                                facecolor="#f7f7f7", edgecolor="none",
                                transform=ax.transAxes, zorder=0)
            ax.add_patch(bg)

        ax.text(0.18, y_center, str(lv),
                ha="center", va="center",
                fontsize=17, fontweight="bold", color=home_color,
                fontfamily=FONT_BOLD, transform=ax.transAxes)

        ax.text(0.5, y_center, label,
                ha="center", va="center",
                fontsize=13, color=TEXT_MID,
                fontfamily=FONT_MAIN, transform=ax.transAxes)

        ax.text(0.82, y_center, str(rv),
                ha="center", va="center",
                fontsize=17, fontweight="bold", color=away_color,
                fontfamily=FONT_BOLD, transform=ax.transAxes)

    # Thin border around the whole table
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(DIVIDER_CLR)
        spine.set_linewidth(0.8)


# ══════════════════════════════════════════════════════════════════════════
# SECTION 3A – SHOT MAP (half-pitch, per team)
# ══════════════════════════════════════════════════════════════════════════

def _draw_shot_map(ax: plt.Axes, match_data: dict,
                   team_side: str, team_name: str, color_val: str) -> None:
    """
    Half-pitch (attacking end) shot map on a subplot axes.
    Green filled circles = goals; transparent circles = non-goals.
    Circle size scales with per-shot xG.
    Mirrors shotmap_whoscored.py draw_shotmap() logic.
    """
    pitch = VerticalPitch(
        pitch_type="statsbomb",
        half=True,
        pitch_color=PITCH_WHITE,
        line_color=PITCH_LINE_DARK,
        linewidth=1.5,
    )
    pitch.draw(ax=ax)

    # Push pitch down by extending the top of the y-axis — title text (axes fraction)
    # then sits in clear white space above the pitch drawing.
    _ymin, _ymax = ax.get_ylim()
    ax.set_ylim(_ymin, _ymax + (_ymax - _ymin) * 0.26)

    try:
        df = build_shot_df(match_data, team_name)
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        ax.text(0.5, 0.5, "No shots", ha="center", va="center",
                fontsize=11, color="white", transform=ax.transAxes)
        ax.text(0.5, 0.97, f"{team_name} — Shot Map",
                ha="center", va="top", fontsize=11, fontweight="bold", color=TEXT_DARK,
                fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=5)
        return

    import matplotlib.colors as mcolors
    xg_vals = df["xG"].values
    norm = mcolors.Normalize(vmin=0, vmax=max(float(xg_vals.max()), 0.4))
    XG_CMAP = plt.cm.RdYlGn

    for _, row in df.iterrows():
        is_goal    = bool(row["is_goal"])
        is_big     = bool(row.get("big_chance", False)) or row.get("situation") == "Penalty"
        base_size  = 500 * float(row["xG"])
        size       = base_size * 3 if is_big else base_size
        size       = max(size, 25)

        colour = color_val if is_goal else "none"
        edge   = "white"  if is_goal else "#666666"
        lw     = 2.0      if is_goal else 1.0
        marker = "o"

        pitch.scatter(
            row["x"], row["y"],
            s=size, marker=marker,
            color=colour,
            edgecolors=edge,
            linewidth=lw,
            alpha=0.92,
            zorder=3,
            ax=ax,
        )

    n_shots   = len(df)
    n_goals   = int(df["is_goal"].sum())
    n_target  = int(df["is_on_target"].sum())
    total_xg  = float(df["xG"].sum())

    ax.text(0.5, 0.97, f"{team_name} — Shot Map",
            ha="center", va="top", fontsize=17, fontweight="bold", color=TEXT_DARK,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=5)
    ax.text(0.5, 0.89,
            f"Shots {n_shots}  |  On Target {n_target}  |  Goals {n_goals}  |  xG {total_xg:.2f}",
            ha="center", va="top", fontsize=14, fontweight="bold", color=TEXT_MID,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=5)

    # Mini legend
    legend_handles = [
        Line2D([0], [0], marker="o", color=PITCH_WHITE, markersize=10,
               markerfacecolor=color_val, markeredgecolor="#444444",
               markeredgewidth=1.5, label="Goal"),
        Line2D([0], [0], marker="o", color=PITCH_WHITE, markersize=8,
               markerfacecolor="none", markeredgecolor="#666666",
               markeredgewidth=1.0, label="No Goal"),
    ]
    leg = ax.legend(handles=legend_handles, loc="lower left",
                    fontsize=11, framealpha=0.85,
                    facecolor="#eeeeee", edgecolor="#aaaaaa",
                    labelcolor="#111111")


# ══════════════════════════════════════════════════════════════════════════
# SECTION 3B – FINAL THIRD ENTRIES MAP (full horizontal pitch, both teams)
# ══════════════════════════════════════════════════════════════════════════

def _draw_final_third_entries(ax: plt.Axes, match_data: dict,
                               home_name: str, home_color: str,
                               away_name: str, away_color: str) -> None:
    """
    Full horizontal pitch split down the center.
    Home (left side → right goal): entries into final third (x crosses 80).
    Away (right side → left goal): entries mirrored (flipped around x=60).

    Channel breakdown (Left / Central / Right wing) shown as text annotation.
    """
    pitch = Pitch(pitch_type="statsbomb",
                  pitch_color=PITCH_WHITE,
                  line_color=PITCH_LINE_DARK,
                  linewidth=1.2)
    pitch.draw(ax=ax)

    # Push pitch down to create whitespace at top for the title
    _ymin, _ymax = ax.get_ylim()
    ax.set_ylim(_ymin, _ymax + (_ymax - _ymin) * 0.18)

    SCALE_X = 1.2

    def _extract_entries(team_side: str) -> pd.DataFrame:
        tid = match_data.get(team_side, {}).get("teamId")
        rows = []
        for ev in match_data.get("events", []):
            if ev.get("teamId") != tid:
                continue
            if ev.get("type", {}).get("displayName") != "Pass":
                continue
            outcome = ev.get("outcomeType", {}).get("displayName", "")
            ex = ev.get("endX")
            ey = ev.get("endY")
            if ex is None or ey is None:
                continue
            x     = ev.get("x", 0) * SCALE_X
            y     = 80 - ev.get("y", 0) * SCALE_Y
            end_x = float(ex) * SCALE_X
            end_y = 80 - float(ey) * SCALE_Y
            if x < 80 and end_x >= 80:
                rows.append({"x": x, "y": y, "end_x": end_x, "end_y": end_y,
                              "success": outcome == "Successful"})
        return pd.DataFrame(rows)

    home_df = _extract_entries("home")
    away_df = _extract_entries("away")

    # Mirror HOME entries so they appear on the LEFT side (matching dashboard layout)
    if not home_df.empty:
        home_df_m = home_df.copy()
        home_df_m["x"]     = 120 - home_df["x"]
        home_df_m["end_x"] = 120 - home_df["end_x"]
        home_df_m["y"]     = home_df["y"]
        home_df_m["end_y"] = home_df["end_y"]
    else:
        home_df_m = pd.DataFrame()

    PASS_MADE   = "#2e9e4f"   # made passes → green
    PASS_UNMADE = "#d6312b"   # unmade passes → red

    def _plot_entries(df: pd.DataFrame, color: str, alpha: float = 0.55) -> None:
        if df.empty:
            return
        # Draw successful passes on top of failed ones so green stays legible
        for _, row in df.sort_values("success").iterrows():
            arrow_color = PASS_MADE if row["success"] else PASS_UNMADE
            ax.annotate("",
                xy=(row["end_x"], row["end_y"]),
                xytext=(row["x"], row["y"]),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color=arrow_color,
                    linewidth=1.2,
                    alpha=alpha,
                    connectionstyle="arc3,rad=0.0",
                ),
                zorder=3 if row["success"] else 2,
            )

    _plot_entries(home_df_m, home_color, alpha=0.6)   # home on LEFT
    _plot_entries(away_df,   away_color, alpha=0.6)   # away on RIGHT

    # ── Channel breakdown labels ──────────────────────────────────────
    def _channel_counts(df: pd.DataFrame) -> dict[str, int]:
        if df.empty:
            return {"L": 0, "C": 0, "R": 0}
        ly = df["end_y"]
        return {
            "L": int((ly < 26.67).sum()),
            "C": int(((ly >= 26.67) & (ly <= 53.33)).sum()),
            "R": int((ly > 53.33).sum()),
        }

    hc = _channel_counts(home_df)
    ac = _channel_counts(away_df)

    # Home channel bar — LEFT side (matches dashboard layout)
    bbox_h_chan = dict(boxstyle="round,pad=0.2", facecolor="#ffffff", edgecolor="none", alpha=0.75)
    for i, (label, key) in enumerate([("LW", "L"), ("CTR", "C"), ("RW", "R")]):
        ax.text(2, 15 + i * 25, f"{label}: {hc[key]}",
                ha="left", va="center", fontsize=12,
                color=home_color, fontfamily=FONT_MAIN, fontweight="bold", zorder=5, bbox=bbox_h_chan)

    # Away channel bar — RIGHT side (matches dashboard layout)
    bbox_a_chan = dict(boxstyle="round,pad=0.2", facecolor="#ffffff", edgecolor="none", alpha=0.75)
    for i, (label, key) in enumerate([("LW", "L"), ("CTR", "C"), ("RW", "R")]):
        ax.text(118, 15 + i * 25, f"{label}: {ac[key]}",
                ha="right", va="center", fontsize=12,
                color=away_color, fontfamily=FONT_MAIN, fontweight="bold", zorder=5, bbox=bbox_a_chan)

    # Success / attempt counts
    n_h       = len(home_df)
    n_a       = len(away_df)
    n_h_succ  = int(home_df["success"].sum()) if not home_df.empty else 0
    n_a_succ  = int(away_df["success"].sum()) if not away_df.empty else 0
    h_pct     = f"{100 * n_h_succ // n_h}%" if n_h else "—"
    a_pct     = f"{100 * n_a_succ // n_a}%" if n_a else "—"

    # Team counts — single centered line below the pitch, split by |
    ax.text(0.488, -0.04, f"{home_name}  {n_h_succ}/{n_h} made ({h_pct})",
            ha="right", va="top", fontsize=15, fontweight="bold", color=home_color,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=5)
    ax.text(0.500, -0.04, "  |  ",
            ha="center", va="top", fontsize=15, color=TEXT_MID,
            fontfamily=FONT_MAIN, transform=ax.transAxes, zorder=5)
    ax.text(0.512, -0.04, f"{away_name}  {n_a_succ}/{n_a} made ({a_pct})",
            ha="left", va="top", fontsize=15, fontweight="bold", color=away_color,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=5)

    ax.text(0.5, 0.98, "Final Third Passes",
            ha="center", va="top", fontsize=15, fontweight="bold", color=TEXT_DARK,
            fontfamily=FONT_BOLD, transform=ax.transAxes, zorder=5)


# ══════════════════════════════════════════════════════════════════════════
# LINEUP PANEL
# ══════════════════════════════════════════════════════════════════════════

def _lineup_extras(match_data: dict):
    """Scan the event stream once for per-playerId goals, assists, and the
    minute each player was subbed on / off, plus the match's final minute."""
    goals: dict = {}
    assists: dict = {}
    on_min: dict = {}
    off_min: dict = {}
    end_min = 90
    for e in match_data.get("events", []):
        # Penalty-shootout kicks aren't goals and must not stretch the timeline to 131'.
        _p = e.get("period", {})
        if isinstance(_p, dict) and (_p.get("value") == 5 or "Shoot" in (_p.get("displayName") or "")):
            continue
        m = e.get("minute") or 0
        if m > end_min:
            end_min = m
        pid = e.get("playerId")
        if pid is None:
            continue
        t = e.get("type", {}).get("displayName", "")
        quals = {q.get("type", {}).get("displayName", "") for q in e.get("qualifiers", [])}
        if t == "Goal" and "OwnGoal" not in quals:
            goals[pid] = goals.get(pid, 0) + 1
        if "IntentionalGoalAssist" in quals:
            assists[pid] = assists.get(pid, 0) + 1
        if t == "SubstitutionOn":
            on_min[pid] = m
        elif t == "SubstitutionOff":
            off_min[pid] = m
    return goals, assists, on_min, off_min, end_min


def _short_player_name(p: dict) -> str:
    """First-initial + surname (or surname only) from a player record."""
    name = _ascii_name(p.get("name", ""))
    parts = name.split()
    return f"{parts[0][0]}. {parts[-1]}" if len(parts) >= 2 else name


def _rating_color(r_val: float) -> str:
    return ("#1a8a1a" if r_val >= 7.5 else
            "#5b9e1e" if r_val >= 6.5 else
            TEXT_MID  if r_val >= 6.0 else
            "#cc4400")


def _draw_lineup(ax: plt.Axes, match_data: dict, side: str,
                 team_name: str, team_color: str, flip: bool = False) -> None:
    """Draw the starting XI plus used substitutes, with shirt numbers, names,
    ratings, goal/assist markers and substitution minutes."""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    players = match_data.get(side, {}).get("players", [])
    _POS_ORDER = {
        "GK": 0,
        "DR": 1, "DC": 1, "DL": 1,
        "DMC": 2,
        "MR": 3, "MC": 3, "ML": 3,
        "AMR": 4, "AMC": 4, "AML": 4,
        "SS": 5, "FW": 6,
    }
    starters = [p for p in players if p.get("isFirstEleven")]
    starters = sorted(starters, key=lambda p: (_POS_ORDER.get(p.get("position", ""), 99), p.get("shirtNo", 99)))

    goals, assists, on_min, off_min, end_min = _lineup_extras(match_data)

    # Subs that actually came on (skip the unused bench).
    subs = [p for p in players
            if not p.get("isFirstEleven")
            and (p.get("playerId") in on_min or p.get("subbedInExpandedMinute") is not None)]
    subs.sort(key=lambda p: on_min.get(p.get("playerId"), p.get("subbedInExpandedMinute") or 999))

    has_ratings = any(bool(p.get("stats", {}).get("ratings")) for p in starters)

    ax.text(
        0.5, 0.985,
        f"{team_name}\nLineup",
        ha="center", va="top",
        fontsize=13.5, fontweight="bold",
        color=TEXT_DARK,
        transform=ax.transAxes,
    )

    if not starters:
        ax.text(0.5, 0.5, "No lineup data", ha="center", va="center",
                fontsize=9, color=TEXT_MID, transform=ax.transAxes)
        return

    # Column anchors (mirrored for the away/flip side).
    if not flip:
        shirt_x, name_x, name_ha = 0.05, 0.12, "left"
        min_x, goal_x, ast_x, rate_x = 0.62, 0.78, 0.86, 0.95
    else:
        shirt_x, name_x, name_ha = 0.95, 0.88, "right"
        min_x, goal_x, ast_x, rate_x = 0.38, 0.22, 0.14, 0.05

    def _markers(y, pid):
        g = goals.get(pid, 0)
        a = assists.get(pid, 0)
        if g:
            gtxt = "●" * g if g <= 3 else f"● x{g}"
            ax.text(goal_x, y, gtxt, ha="center", va="center",
                    fontsize=10.2, color="#111111", transform=ax.transAxes)
        if a:
            atxt = "A" if a == 1 else f"A{a}"
            ax.text(ast_x, y, atxt, ha="center", va="center",
                    fontsize=10.4, fontweight="bold", color="#c8881b",
                    transform=ax.transAxes)

    def _rating(y, p, fs):
        if not has_ratings:
            return
        rd = p.get("stats", {}).get("ratings", {})
        rating = list(rd.values())[-1] if rd else None
        if rating is not None:
            rv = float(rating)
            ax.text(rate_x, y, f"{rv:.1f}", ha="center", va="center",
                    fontsize=fs, fontweight="bold", color=_rating_color(rv),
                    transform=ax.transAxes)

    n_sub = len(subs)
    total_rows = len(starters) + (1 + n_sub if n_sub else 0)
    avail = 0.90
    row_h = avail / max(total_rows, 1)
    fs = 14.0 if total_rows <= 13 else 12.0
    y = 0.90

    # ── Starting XI ───────────────────────────────────────────────────
    for p in starters:
        pid = p.get("playerId")
        ax.text(shirt_x, y, str(p.get("shirtNo", "")), ha="center", va="center",
                fontsize=fs - 0.7, fontweight="bold", color=team_color,
                transform=ax.transAxes)
        ax.text(name_x, y, _short_player_name(p), ha=name_ha, va="center",
                fontsize=fs, color=TEXT_DARK, transform=ax.transAxes, clip_on=True)
        if pid in off_min:
            ax.text(min_x, y, f"↓{off_min[pid]}'", ha="center", va="center",
                    fontsize=9.4, color="#cc4400", transform=ax.transAxes)
        _markers(y, pid)
        _rating(y, p, fs - 0.5)
        y -= row_h

    # ── Substitutes ───────────────────────────────────────────────────
    if subs:
        # Surname lookup for "came on for <player>" labels.
        surname_by_pid: dict = {}
        for pp in players:
            nm = _ascii_name(pp.get("name", ""))
            surname_by_pid[pp.get("playerId")] = nm.split()[-1] if nm.split() else nm
        out_x, ha_out = (0.70, "right") if not flip else (0.30, "left")

        ax.axhline(y + row_h * 0.5, xmin=0.03, xmax=0.97,
                   color=DIVIDER_CLR, linewidth=0.6)
        ax.text(name_x, y, "SUBS", ha=name_ha, va="center",
                fontsize=9.4, fontweight="bold", color=TEXT_MID,
                transform=ax.transAxes)
        y -= row_h
        for p in subs:
            pid = p.get("playerId")
            on = on_min.get(pid, p.get("subbedInExpandedMinute"))
            out_name = surname_by_pid.get(p.get("subbedOutPlayerId"), "")
            if out_name and on is not None:
                info = f"for {out_name} {on}'"
            elif on is not None:
                info = f"{on}'"
            else:
                info = ""
            ax.text(shirt_x, y, str(p.get("shirtNo", "")), ha="center", va="center",
                    fontsize=fs - 1.3, fontweight="bold", color=team_color,
                    alpha=0.85, transform=ax.transAxes)
            ax.text(name_x, y, _short_player_name(p), ha=name_ha, va="center",
                    fontsize=fs - 0.8, color=TEXT_MID, transform=ax.transAxes,
                    clip_on=True)
            if info:
                ax.text(out_x, y, info, ha=ha_out, va="center",
                        fontsize=9.0, color=TEXT_LIGHT, transform=ax.transAxes,
                        clip_on=True)
            _markers(y, pid)
            _rating(y, p, fs - 1.0)
            y -= row_h


# ══════════════════════════════════════════════════════════════════════════
# MAIN RENDER FUNCTION
# ══════════════════════════════════════════════════════════════════════════

def render_wc_dashboard(match_data: dict, output_path: str) -> str:
    """
    Render the complete WC 2026 analytics dashboard as a high-resolution PNG.

    Args:
        match_data:   WhoScored-compatible dict with 'meta', 'home', 'away',
                      'events', and 'match_stats' keys.
        output_path:  Full path for the output .png file.

    Returns:
        output_path on success.
    """
    home_d = match_data.get("home", {})
    away_d = match_data.get("away", {})
    home_name = home_d.get("name", "Home")
    away_name = away_d.get("name", "Away")

    # Fail safe: refuse to render an empty stub (a crashed/timed-out scrape that
    # produced no events and no lineups). Otherwise the stats table divides by
    # zero and the run dies with an opaque "division by zero". Raising a clear
    # error lets run_match log the real reason and the catch-up sweep retry it.
    if not (match_data.get("events") or []) \
            and not (home_d.get("players") or []) \
            and not (away_d.get("players") or []):
        raise ValueError(
            f"Refusing to render {home_name} vs {away_name}: match JSON is an "
            f"empty stub (no events, no lineups) — the scrape returned no data."
        )

    home_colors = get_team_colors(home_name, fallback_home=True)
    away_colors = get_team_colors(away_name, fallback_home=False)
    if home_d.get("primary_color"):
        home_colors["primary"] = home_d["primary_color"]
    if away_d.get("primary_color"):
        away_colors["primary"] = away_d["primary_color"]

    home_color = home_colors["primary"]
    away_color = away_colors["primary"]

    log.info("Rendering dashboard: %s vs %s", home_name, away_name)

    # ── Figure & GridSpec ────────────────────────────────────────────
    fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor=CANVAS_BG)
    gs  = GridSpec(
        nrows=3, ncols=3,
        figure=fig,
        height_ratios=[1.35, 5.50, 5.00],
        hspace=0.08,
        wspace=0.04,
        left=0.01, right=0.99,
        top=0.98, bottom=0.01,
    )

    # ── Header ──────────────────────────────────────────────────────
    ax_header = fig.add_subplot(gs[0, :])
    _draw_header(fig, ax_header, match_data)

    # ── Vertical dividers between main sections ───────────────────
    # (Handled via tight_layout spacing + individual subplot borders)

    # ── Middle row — 5-column layout: lineup | pass-net | stats | pass-net | lineup
    gs_mid = gs[1, :].subgridspec(
        1, 5,
        width_ratios=[2.35, 1.8, 3.0, 1.8, 2.35],
        wspace=0.012,
    )
    ax_lu_home  = fig.add_subplot(gs_mid[0, 0])
    ax_pn_home  = fig.add_subplot(gs_mid[0, 1])
    ax_stats    = fig.add_subplot(gs_mid[0, 2])
    ax_pn_away  = fig.add_subplot(gs_mid[0, 3])
    ax_lu_away  = fig.add_subplot(gs_mid[0, 4])

    _draw_lineup(ax_lu_home, match_data, "home", home_name, home_color, flip=False)
    _draw_pass_network(ax_pn_home, match_data, "home", home_name, home_color)
    _draw_stats_table(ax_stats,    match_data, home_name, away_name, home_color, away_color)
    _draw_pass_network(ax_pn_away, match_data, "away", away_name, away_color)
    _draw_lineup(ax_lu_away, match_data, "away", away_name, away_color, flip=True)

    # Section dividers
    for ax in (ax_lu_home, ax_pn_home, ax_stats, ax_pn_away, ax_lu_away):
        for spine in ax.spines.values():
            spine.set_color(DIVIDER_CLR)
            spine.set_linewidth(0.7)

    # ── Bottom row ───────────────────────────────────────────────────
    ax_sm_home  = fig.add_subplot(gs[2, 0])
    ax_ft       = fig.add_subplot(gs[2, 1])
    ax_sm_away  = fig.add_subplot(gs[2, 2])

    _draw_shot_map(ax_sm_home, match_data, "home", home_name, home_color)
    _draw_final_third_entries(ax_ft, match_data, home_name, home_color, away_name, away_color)
    _draw_shot_map(ax_sm_away, match_data, "away", away_name, away_color)

    for ax in (ax_sm_home, ax_ft, ax_sm_away):
        for spine in ax.spines.values():
            spine.set_color(DIVIDER_CLR)
            spine.set_linewidth(0.7)

    # ── Save ─────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fig.savefig(output_path, dpi=FIG_DPI, bbox_inches="tight",
                facecolor=CANVAS_BG, edgecolor="none")
    plt.close(fig)

    log.info("Dashboard saved → %s", output_path)

    # Keep the web dashboard in sync: every time a finished game is rendered,
    # rebuild the interactive match page for it and regenerate the index data.js.
    # Never let this break a render. Name the match-centre detail after the OUTPUT
    # file (the slot-coded id for knockout games) so the PNG, the detail JS and the
    # dashboard match id all line up.
    _match_id = os.path.splitext(os.path.basename(output_path))[0]
    _refresh_web_dashboard_db(match_data, _match_id)

    return output_path


def _load_dashboard_module(name: str, filename: str):
    import importlib.util
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo_root, "laliga_dashboard", filename)
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _refresh_web_dashboard_db(match_data: dict | None = None, match_id: str | None = None) -> None:
    """Rebuild the interactive match page + the index data.js (best-effort).

    ``match_id`` forces the detail filename (the slot-coded id for knockout games) so it
    matches the PNG and the dashboard match id; when omitted it's derived from team names."""
    try:
        if match_data is not None:
            details = _load_dashboard_module("wc_dashboard_details", "build_match_details.py")
            if details is not None:
                out = details.write_detail(match_data, match_id)
                if out:
                    log.info("Match centre page refreshed → %s", os.path.basename(out))
    except Exception as exc:  # pragma: no cover - never block rendering
        log.warning("Could not refresh match centre page: %s", exc)
    for modname, filename, label in (
        ("wc_dashboard_build", "build_data.py", "data.js"),
        ("wc_dashboard_players", "build_players.py", "players.js"),
        ("wc_dashboard_shots", "build_shots.py", "shots.js (Team Lab)"),
        ("wc_dashboard_database", "build_database.py", "database export"),
        ("wc_dashboard_player_lab", "build_player_lab.py", "player_lab per-team events"),
    ):
        try:
            mod = _load_dashboard_module(modname, filename)
            if mod is not None:
                mod.main()
                log.info("Web dashboard refreshed (%s)", label)
        except Exception as exc:  # pragma: no cover - never block rendering
            log.warning("Could not refresh %s: %s", label, exc)


def output_filename(match_data: dict, output_dir: str = ".") -> str:
    """
    Generate canonical output filename:
      YYYY_MM_DD_[HomeTeam]_vs_[AwayTeam].png
    """
    meta   = match_data.get("meta") or {}
    date   = (meta.get("date") or match_data.get("date") or "2026_06_01").replace("-", "_")
    home   = match_data.get("home", {}).get("name", "Home").replace(" ", "_")
    away   = match_data.get("away", {}).get("name", "Away").replace(" ", "_")
    fname  = f"{date}_{home}_vs_{away}.png"
    return os.path.join(output_dir, fname)
