#!/usr/bin/env python3
"""Repair the shot coordinates of already-shipped FotMob-only match detail files.

Background
----------
When WhoScored has no event stream for a game, ``laliga/scraper.py`` synthesises
shot events from FotMob's shotmap. That converter had two bugs:

  1. it treated FotMob's coordinates as 0-100 percentages when they are pitch
     METRES on a 105 x 68 field, so every shot was squashed towards the halfway
     line and the touchline (and the model xG collapsed with it);
  2. it mirrored the away team (``100 - x``), but WhoScored — the shape the whole
     pipeline speaks — keeps both sides in their own attacking frame and the
     drawing code (match.js ``tx``/``ty``, renderer.py) does the mirroring. The
     double flip put the away team's shots at the *home* team's end.

``scraper.py`` is fixed, but the raw scrapes are git-ignored and FotMob is not
reachable from every environment, so the affected ``matches_detail/<id>.js`` files
have to be repaired in place. Both bugs are exact, invertible transforms, so the
original metre coordinates can be recovered and re-projected correctly.

What is NOT recoverable here: FotMob's own per-shot xG, the body part and the
situation (the buggy converter only ever emitted LeftFoot/RightFoot and "Open
Play"). Those come back on a re-scrape of the match. xG is therefore recomputed
from the corrected coordinates with the repo's own model.

Usage:
    py laliga_dashboard/tools/fix_fotmob_shot_coords.py [--dry-run]
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.dirname(HERE)
ROOT = os.path.dirname(DASH)
sys.path.insert(0, DASH)
sys.path.insert(0, ROOT)

from xg_model import match_xg_by_event, shot_xg   # noqa: E402

DETAIL_DIR = os.path.join(DASH, "matches_detail")

FM_PITCH_X = 105.0
FM_PITCH_Y = 68.0

BODY_QUAL = {"Right Foot": "RightFoot", "Left Foot": "LeftFoot", "Header": "Head"}
SIT_QUAL = {"Penalty": "Penalty", "Free Kick": "DirectFreekick", "Fast Break": "FastBreak",
            "Set Piece": "SetPiece", "Corner": "FromCorner"}


def read_detail(path):
    txt = open(path, encoding="utf-8").read()
    m = re.match(r"\s*window\.MATCH_DETAIL\s*=\s*(\{.*\})\s*;?\s*$", txt, re.S)
    if not m:
        raise ValueError("not a MATCH_DETAIL file: " + path)
    return json.loads(m.group(1))


def write_detail(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("window.MATCH_DETAIL = " + json.dumps(data, separators=(",", ":")) + ";\n")


def is_fotmob_only(d):
    """A match built from FotMob's shotmap alone: shots but no event stream."""
    return bool(d.get("shots")) and not d.get("passes") and not d.get("dribbles") \
        and not d.get("saves")


def needs_fix(d):
    """True while the shots still carry the buggy projection.

    The tell is unambiguous: the away side mirrored into 0-100 metres lands its
    shots near x=0 (own goal) and, because 105 metres were read as 100, some
    coordinates fall outside 0-100 entirely.
    """
    if d.get("shotFrame") == "ws":          # already repaired, marked below
        return False
    shots = d.get("shots") or []
    if any(s["x"] < 0 or s["x"] > 100 or s["y"] < 0 or s["y"] > 100 for s in shots):
        return True
    away = [s["x"] for s in shots if s["team"] == "away"]
    home = [s["x"] for s in shots if s["team"] == "home"]
    # both sides attack towards x=100 in the WhoScored frame; a mirrored away side
    # sits on the opposite half from the home side
    return bool(away) and bool(home) and max(away) < 50 <= max(home)


def fix_shot_xy(s):
    """Undo the away mirror, then rescale 105 x 68 metres to WhoScored 0-100."""
    xm, ym = s["x"], s["y"]
    if s["team"] == "away":
        xm, ym = 100.0 - xm, 100.0 - ym
    s["x"] = round(min(100.0, max(0.0, xm / FM_PITCH_X * 100.0)), 1)
    s["y"] = round(min(100.0, max(0.0, ym / FM_PITCH_Y * 100.0)), 1)


def as_event(s, team_id):
    """A WhoScored-shaped shot event for the xG model, from a detail-file shot."""
    quals = []
    if s.get("body") in BODY_QUAL:
        quals.append({"type": {"displayName": BODY_QUAL[s["body"]]}})
    if s.get("sit") in SIT_QUAL:
        quals.append({"type": {"displayName": SIT_QUAL[s["sit"]]}})
    if s.get("big"):
        quals.append({"type": {"displayName": "BigChance"}})
    if s.get("gy") is not None:
        quals.append({"type": {"displayName": "GoalMouthY"}, "value": s["gy"]})
    if s.get("gz") is not None:
        quals.append({"type": {"displayName": "GoalMouthZ"}, "value": s["gz"]})
    tname = ("Goal" if s["goal"] else "BlockedShot" if s["blocked"]
             else "ShotOnPost" if s.get("post") else "SavedShot" if s["onTarget"]
             else "MissedShots")
    return {
        "eventId": None,
        "minute": s["min"], "second": s.get("sec", 0),
        "teamId": team_id, "x": s["x"], "y": s["y"],
        "period": {"displayName": "FirstHalf" if s["min"] <= 45 else "SecondHalf",
                   "value": 1 if s["min"] <= 45 else 2},
        "type": {"displayName": tname, "value": 16 if s["goal"] else 13},
        "outcomeType": {"displayName": "Successful" if s["goal"] else "Unsuccessful",
                        "value": 1 if s["goal"] else 0},
        "qualifiers": quals,
        "playerId": s.get("player"),
    }


def rescore(d):
    """Recompute model xG for every shot from the corrected coordinates."""
    events = [as_event(s, s["team"]) for s in d["shots"]]
    fake_match = {"events": events, "home": {"teamId": "home"}, "away": {"teamId": "away"}}
    by_event = match_xg_by_event(fake_match)
    for s, ev in zip(d["shots"], events):
        s["xg"], _ = shot_xg(ev, by_event)
    # keep the line-up cards' per-player xG in step with the rescored shots
    per_player = {}
    for s in d["shots"]:
        per_player[s["player"]] = per_player.get(s["player"], 0.0) + s["xg"]
    for side in ("home", "away"):
        for group in ("starters", "subs"):
            for p in d.get("lineups", {}).get(side, {}).get(group, []):
                p["xg"] = round(per_player.get(p["name"], 0.0), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    fixed = 0
    for path in sorted(glob.glob(os.path.join(DETAIL_DIR, "*.js"))):
        if os.path.basename(path).startswith("_"):
            continue
        d = read_detail(path)
        if not is_fotmob_only(d) or not needs_fix(d):
            continue
        before = sum(s["xg"] for s in d["shots"])
        for s in d["shots"]:
            fix_shot_xy(s)
        rescore(d)
        d["shotFrame"] = "ws"      # repaired; also stops this script re-running on it
        d["src"] = "fotmob"        # shots only — no pass/dribble stream for this match
        after = sum(s["xg"] for s in d["shots"])
        print("%s  %d shots  xG %.2f -> %.2f" %
              (os.path.basename(path), len(d["shots"]), before, after))
        if not args.dry_run:
            write_detail(path, d)
        fixed += 1
    print("%d file(s) %s" % (fixed, "would be fixed" if args.dry_run else "fixed"))


if __name__ == "__main__":
    main()
