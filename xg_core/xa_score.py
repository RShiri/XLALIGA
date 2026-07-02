"""XAScorer — pure-python runtime scoring of the exported xa_artifact.json.

This file + xa_features.py + features.py + xa_artifact.json are what you
vendor into each repo's build pipeline. No numpy/sklearn needed; if lightgbm
is installed the scorer upgrades to the full two-stage GBM path, otherwise it
uses the linear path with its own calibration map — both are calibrated.

xA(pass) = calibrated P(this successful pass becomes a goal assist).
Sum over a player's passes = expected assists. Failed passes score 0 by
definition and are never passed in.
"""
import bisect
import json
import math
import os

try:
    from .xa_features import PASS_FEATURE_NAMES, pass_feature_dict, quals_of
    from .features import ws_to_sb, is_shootout
except ImportError:  # vendored flat next to the build scripts
    from xa_features import PASS_FEATURE_NAMES, pass_feature_dict, quals_of
    from features import ws_to_sb, is_shootout

_DEFAULT_ARTIFACT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "xa_artifact.json")


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


class XAScorer:
    def __init__(self, artifact_path=None):
        with open(artifact_path or _DEFAULT_ARTIFACT, encoding="utf-8") as f:
            self.art = json.load(f)
        self._gbm_shot = self._gbm_xg = None
        self._full = False
        if self.art.get("shot_gbm") and self.art.get("xg_gbm"):
            try:  # optional upgrade — never required
                import lightgbm as lgb
                self._gbm_shot = lgb.Booster(model_str=self.art["shot_gbm"])
                self._gbm_xg = lgb.Booster(model_str=self.art["xg_gbm"])
                self._full = True
            except Exception:
                pass

    def xa_from_features(self, feats, league=None):
        """feats: dict from xa_features.pass_feature_dict(). Calibrated xA."""
        lr = self.art["shot_lr"]
        zA = lr["intercept"] + sum(lr["coef"][f] * feats[f]
                                   for f in PASS_FEATURE_NAMES)
        if self._full:
            row = [[feats[f] for f in PASS_FEATURE_NAMES]]
            w = self.art["blend"]["w_gbm"]
            if w > 0:
                zg = _logit(float(self._gbm_shot.predict(row)[0]))
                zA = (1 - w) * zA + w * zg
            zB = float(self._gbm_xg.predict(row)[0])
            p = _apply_calibrator(self.art["calibrator"],
                                  _sigmoid(zA) * _sigmoid(zB))
        else:
            lin = self.art["xg_linreg"]
            zB = lin["intercept"] + sum(lin["coef"][f] * feats[f]
                                        for f in PASS_FEATURE_NAMES)
            p = _apply_calibrator(self.art["calibrator_lr_only"],
                                  _sigmoid(zA) * _sigmoid(zB))
        shifts = self.art.get("league_shifts", {})
        shift = shifts.get(league) if league else None
        if shift is None:
            shift = shifts.get("_global")
        if shift and p > 0:
            p = _sigmoid(_logit(p) + shift)
        lo, hi = self.art.get("clip", [0.0, 0.6])
        return round(min(max(p, lo), hi), 4)

    def estimate_xa(self, x_ws, y_ws, end_x_ws, end_y_ws, quals=frozenset(),
                    league=None):
        """Score one successful pass from WhoScored fields: start/end coords
        (WhoScored 0-100 scale) + the set of qualifier displayNames."""
        x_sb, y_sb = ws_to_sb(x_ws, y_ws)
        ex_sb, ey_sb = ws_to_sb(end_x_ws, end_y_ws)
        return self.xa_from_features(
            pass_feature_dict(x_sb, y_sb, ex_sb, ey_sb, quals), league=league)

    def player_xa_from_events(self, match_data, league=None):
        """playerId -> summed xA over every successful pass in the match.

        Drop-in replacement for xg_model.player_xa_from_events (the shot-based
        version): same signature shape, but credit accrues on the PASS, so a
        killer ball the striker wastes still counts, and no shot is required."""
        out = {}
        for ev in match_data.get("events", []):
            t = ev.get("type", {})
            if not isinstance(t, dict) or t.get("displayName") != "Pass":
                continue
            if is_shootout(ev):
                continue
            if (ev.get("outcomeType", {}) or {}).get("displayName") != "Successful":
                continue
            pid = ev.get("playerId")
            if pid is None:
                continue
            xa = self.estimate_xa(ev.get("x", 0.0), ev.get("y", 0.0),
                                  ev.get("endX", ev.get("x", 0.0)),
                                  ev.get("endY", ev.get("y", 0.0)),
                                  quals_of(ev), league=league)
            if xa > 0:
                out[pid] = out.get(pid, 0.0) + xa
        return out
