"""XGScorer — pure-python runtime scoring of the exported xg_artifact.json.

This file + features.py + xg_artifact.json are what you vendor into each repo's
build pipeline (they replace the hard-coded _INTERCEPT/_COEF block in
laliga_dashboard/xg_model.py and renderer._estimate_xg). No numpy/sklearn needed;
if lightgbm happens to be installed the scorer upgrades itself to the full
LR+GBM+market blend, otherwise it uses the logistic path with its own
calibration map fitted for exactly this fallback — both paths are calibrated.
"""
import bisect
import json
import math
import os

try:
    from .features import FEATURE_NAMES, feature_dict
except ImportError:  # vendored flat next to the build scripts
    from features import FEATURE_NAMES, feature_dict

_DEFAULT_ARTIFACT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "xg_artifact.json")


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


class XGScorer:
    def __init__(self, artifact_path=None):
        with open(artifact_path or _DEFAULT_ARTIFACT, encoding="utf-8") as f:
            self.art = json.load(f)
        self._gbm = self._market = None
        self._full_blend = False
        if self.art.get("gbm"):
            try:  # optional upgrade — never required
                import lightgbm as lgb
                self._gbm = lgb.Booster(model_str=self.art["gbm"])
                if self.art.get("market_distill"):
                    self._market = lgb.Booster(model_str=self.art["market_distill"])
                self._full_blend = True
            except Exception:
                pass

    def xg_from_features(self, feats, league=None):
        """feats: dict from features.feature_dict(). Returns calibrated xG."""
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

    def estimate_xg(self, x_sb, y_sb, is_penalty, is_big_chance, body_part,
                    situation="Open Play", assisted=False, league=None):
        """Drop-in replacement for xg_model.estimate_xg / renderer._estimate_xg
        (same signature + optional assisted/league)."""
        if is_penalty:
            return self.art.get("penalty_xg", 0.76)
        feats = feature_dict(x_sb, y_sb, body_part, situation, is_big_chance,
                             assisted=assisted)
        return self.xg_from_features(feats, league=league)
