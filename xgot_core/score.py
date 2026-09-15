# -*- coding: utf-8 -*-
"""XGOTScorer -- pure-python runtime scoring of xgot_artifact.json.

Same generic scoring math as xg_core_v3/score.py (works for any artifact's
feature_names); the only real difference is the event-based entry point needs
goal-mouth placement, so it only scores shots that reached the frame (Goal or
SavedShot) AND carry GoalMouthY/GoalMouthZ qualifiers -- anything else has no
xGOT by definition (it wasn't on target).

Vendor this + features.py + xgot_artifact.json. stdlib-only; upgrades to the
full LR+GBM+market blend if lightgbm is importable, else falls back to the
calibrated logistic-only path (both carry their own calibration map).
"""
import bisect
import json
import math
import os

try:
    from .features import (FEATURE_NAMES, ON_TARGET_TYPES, is_shootout,
                           _qual_set, base_feature_dict, _extra_features,
                           _assist_pass, ws_to_sb, extract_qualifiers,
                           goal_mouth_coords, placement_features)
except ImportError:  # vendored flat next to the build scripts
    from features import (FEATURE_NAMES, ON_TARGET_TYPES, is_shootout,
                          _qual_set, base_feature_dict, _extra_features,
                          _assist_pass, ws_to_sb, extract_qualifiers,
                          goal_mouth_coords, placement_features)

_DEFAULT_ARTIFACT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "xgot_artifact.json")


def _sigmoid(z):
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _apply_calibrator(cal, p):
    kind = cal.get("kind")
    if kind == "isotonic":
        xs, ys = cal["x"], cal["y"]
        if p <= xs[0]:
            return ys[0]
        if p >= xs[-1]:
            return ys[-1]
        i = bisect.bisect_right(xs, p)
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (p - x0) / (x1 - x0)
    if kind == "platt":
        return _sigmoid(cal["a"] * _logit(p) + cal["b"])
    return p


class XGOTScorer:
    def __init__(self, artifact_path=None):
        with open(artifact_path or _DEFAULT_ARTIFACT, encoding="utf-8") as f:
            self.art = json.load(f)
        self._gbm = self._market = None
        self._full_blend = False
        if self.art.get("gbm"):
            try:  # optional upgrade -- never required
                import lightgbm as lgb
                self._gbm = lgb.Booster(model_str=self.art["gbm"])
                if self.art.get("market_distill"):
                    self._market = lgb.Booster(model_str=self.art["market_distill"])
                self._full_blend = True
            except Exception:
                pass

    def xgot_from_features(self, feats, league=None):
        """feats: dict carrying every name in FEATURE_NAMES. Returns calibrated xGOT."""
        lr = self.art["lr"]
        z = lr["intercept"] + sum(lr["coef"][f] * feats[f] for f in FEATURE_NAMES)
        if self._full_blend:
            row = [[feats[f] for f in FEATURE_NAMES]]
            w = self.art["blend"]["w_gbm"]
            if self._gbm is not None and w > 0:
                zg = _logit(float(self._gbm.predict(row)[0]))
                z = (1 - w) * z + w * zg
            a = self.art["blend"]["w_market"]
            if self._market is not None and a > 0:
                z = (1 - a) * z + a * float(self._market.predict(row)[0])
            p = _apply_calibrator(self.art["calibrator"], _sigmoid(z))
        else:
            p = _apply_calibrator(self.art["calibrator_lr_only"], _sigmoid(z))
        shifts = self.art.get("league_shifts", {})
        shift = shifts.get(league) if league else None
        if shift is None:
            shift = shifts.get("_global")
        if shift:
            p = _sigmoid(_logit(p) + shift)
        lo, hi = self.art.get("clip", [0.002, 0.97])
        return round(min(max(p, lo), hi), 3)

    def xgot_from_shot_event(self, ev, byid, prev_pass, league=None):
        """Full xGOT for one on-target shot event, or None if it isn't eligible
        (not on target, a penalty, a shootout kick, or missing placement coords)."""
        t = ev.get("type", {})
        dn = t.get("displayName") if isinstance(t, dict) else None
        if dn not in ON_TARGET_TYPES or is_shootout(ev):
            return None
        if "OwnGoal" in _qual_set(ev):
            return None
        gy, gz = goal_mouth_coords(ev)
        if gy is None or gz is None:
            return None
        body, situation, big = extract_qualifiers(ev)
        if situation == "Penalty":
            return None  # excluded from training -- see features.py
        x_sb, y_sb = ws_to_sb(ev.get("x", 0), ev.get("y", 0))
        ap = _assist_pass(ev, byid, prev_pass)
        feats = base_feature_dict(x_sb, y_sb, body, situation, big,
                                  assisted=ev.get("relatedPlayerId") is not None)
        feats.update(_extra_features(_qual_set(ev), ap))
        feats.update(placement_features(gy, gz))
        return self.xgot_from_features(feats, league=league)

    def iter_match_xgot(self, match_data, league=None):
        """Yield (event_id, xgot) for every eligible on-target shot in a match."""
        evs = match_data.get("events", [])
        byid = {e.get("eventId"): e for e in evs}
        prev_pass = None
        for ev in evs:
            t = ev.get("type", {})
            dn = t.get("displayName") if isinstance(t, dict) else None
            if dn == "Pass":
                prev_pass = ev
            xgot = self.xgot_from_shot_event(ev, byid, prev_pass, league=league)
            if xgot is not None:
                yield ev.get("eventId"), xgot
