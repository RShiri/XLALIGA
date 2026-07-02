"""CalibratedXGModel — the shared training pipeline for XLALIGA / XWORLDCUP / BCN.

Design (why it looks like this):

* Two base learners over the same engineered features: an L2 logistic regression
  (smooth, extrapolates sanely, exportable as plain coefficients) and a LightGBM
  classifier with MONOTONE constraints on dist/angle (captures interactions the
  LR misses, but is forbidden from learning upward-sloping pockets that inflate
  bad shots). Their logits are blended with a weight chosen on out-of-fold
  predictions by Brier score.

* Market anchoring by DISTILLATION, not stacking. Platform xG (Understat/FotMob/
  Opta) exists only for historical shots, so it can never be an input feature —
  the model would be uncallable on tomorrow's match. Instead a regressor is
  trained to predict logit(market_xg) FROM THE SAME FEATURES; that student model
  is available at inference forever, and its logit is blended in with a weight
  again chosen on out-of-fold Brier against real outcomes. Market data grounds
  the shape; real goals keep the level honest.

* Calibration is cross-fitted: base models predict each shot out-of-fold, the
  calibrator (isotonic, or Platt for small samples) is fitted on those honest
  predictions, then the bases are refitted on all data. Never calibrate on
  in-sample predictions — that is exactly how optimistic bias survives.

* Class imbalance (~10% of shots are goals) is handled by DOING NOTHING to the
  sampling. No SMOTE, no class_weight, no scale_pos_weight: any reweighting
  shifts the base rate and produces systematically inflated probabilities — the
  precise disease being cured. Log-loss on the natural distribution is the
  correct objective for a probability model.

* Everything exports to a JSON artifact scored by score.XGScorer (pure python,
  LightGBM optional), so the three dashboards keep their no-dependency builders.
"""
import datetime
import json
import math
import warnings

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold, StratifiedKFold
from sklearn.preprocessing import StandardScaler

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:  # LR-only mode still works everywhere
    HAS_LGBM = False

from .features import FEATURE_NAMES, MONOTONE

EPS = 1e-6
CLIP = (0.002, 0.97)  # NOT 0.01 — a 0.01 floor alone adds ~0.1 xG per 10 long shots


def _logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.asarray(z, float)))


class _Calibrator:
    """Isotonic (default) or Platt map applied to blended probabilities.
    Serialises to a plain dict the pure-python scorer can evaluate."""

    def __init__(self, kind="isotonic"):
        self.kind = kind
        self.x_ = self.y_ = None      # isotonic knots
        self.a_ = self.b_ = None      # platt: sigmoid(a*logit(p)+b)

    def fit(self, p, y):
        p, y = np.asarray(p, float), np.asarray(y, float)
        # isotonic needs enough positives to place knots reliably
        if self.kind == "isotonic" and y.sum() >= 100:
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y)
            self.x_ = iso.X_thresholds_.tolist()
            self.y_ = iso.y_thresholds_.tolist()
        else:
            self.kind = "platt"
            lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p).reshape(-1, 1), y)
            self.a_ = float(lr.coef_[0][0])
            self.b_ = float(lr.intercept_[0])
        return self

    def apply(self, p):
        p = np.asarray(p, float)
        if self.kind == "isotonic" and self.x_:
            return np.interp(p, self.x_, self.y_)
        if self.kind == "platt" and self.a_ is not None:
            return _sigmoid(self.a_ * _logit(p) + self.b_)
        return p  # identity (unfitted)

    def to_dict(self):
        if self.kind == "isotonic" and self.x_:
            return {"kind": "isotonic", "x": self.x_, "y": self.y_}
        if self.a_ is not None:
            return {"kind": "platt", "a": self.a_, "b": self.b_}
        return {"kind": "identity"}


class CalibratedXGModel:
    """fit / predict_proba / predict_xg / calibrate / evaluate / export.

    fit(X, y, groups=match_ids, market_xg=..., leagues=...) does everything:
    OOF predictions, blend-weight search, cross-fitted calibration, full refit,
    per-league level shifts. X is a DataFrame containing FEATURE_NAMES columns
    (extra columns are ignored) — penalties must already be excluded.
    """

    def __init__(self, calibration="isotonic", n_splits=5, use_gbm=True,
                 random_state=42):
        self.calibration = calibration
        self.n_splits = n_splits
        self.use_gbm = use_gbm and HAS_LGBM
        if use_gbm and not HAS_LGBM:
            warnings.warn("lightgbm not installed — falling back to logistic-only")
        self.random_state = random_state
        self.scaler_ = None
        self.lr_ = None
        self.gbm_ = None
        self.market_ = None            # distilled market-xG regressor
        self.w_gbm_ = 0.0              # blend weight LR vs GBM (logit space)
        self.w_market_ = 0.0           # blend weight model vs market student
        self.calibrator_ = _Calibrator(calibration)
        self.calibrator_lr_ = _Calibrator(calibration)  # pure-python fallback path
        self.league_shifts_ = {}
        self.metrics_ = {}

    # ---------------------------------------------------------------- helpers
    def _frame(self, X):
        # LightGBM sees the DataFrame (stable feature names), LR sees .to_numpy()
        return X[FEATURE_NAMES].astype(float)

    def _new_lr(self):
        return LogisticRegression(C=1.0, max_iter=5000)

    def _new_gbm(self):
        return lgb.LGBMClassifier(
            objective="binary",
            n_estimators=600, learning_rate=0.03,
            num_leaves=15, min_child_samples=80,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            reg_lambda=5.0,
            monotone_constraints=[MONOTONE.get(f, 0) for f in FEATURE_NAMES],
            # deliberately NO is_unbalance / scale_pos_weight — see module docstring
            random_state=self.random_state, verbose=-1,
        )

    def _new_market(self):
        return lgb.LGBMRegressor(
            n_estimators=400, learning_rate=0.05, num_leaves=15,
            min_child_samples=80, subsample=0.8, subsample_freq=1,
            reg_lambda=5.0,
            monotone_constraints=[MONOTONE.get(f, 0) for f in FEATURE_NAMES],
            random_state=self.random_state, verbose=-1,
        )

    @staticmethod
    def _best_blend(z_a, z_b, y, grid=None):
        """Weight w minimising Brier of sigmoid((1-w)*z_a + w*z_b) on OOF data."""
        best_w, best_brier = 0.0, np.inf
        for w in (grid if grid is not None else np.linspace(0, 1, 21)):
            b = brier_score_loss(y, _sigmoid((1 - w) * z_a + w * z_b))
            if b < best_brier:
                best_w, best_brier = float(w), b
        return best_w

    @staticmethod
    def _solve_shift(p, total_goals, lo=-2.0, hi=2.0):
        """Logit shift d with sum(sigmoid(logit(p)+d)) == total_goals (bisection)."""
        z = _logit(p)
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if _sigmoid(z + mid).sum() < total_goals:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    # -------------------------------------------------------------------- fit
    def fit(self, X, y, groups=None, market_xg=None, leagues=None):
        Xdf = self._frame(X)
        Xm = Xdf.to_numpy()
        y = np.asarray(y, dtype=int)
        n = len(y)
        market = None
        if market_xg is not None:
            market = np.asarray(market_xg, dtype=float)
            if not np.isnan(market).all() and np.isnan(market).any():
                pass  # partial coverage is fine — the distill fit masks NaNs
            elif np.isnan(market).all():
                market = None

        # ---- out-of-fold predictions (grouped by match: shots from one match
        # share rebounds/keeper/context and must not straddle train/test)
        if groups is not None:
            splitter = GroupKFold(n_splits=self.n_splits)
            splits = splitter.split(Xm, y, np.asarray(groups))
        else:
            splitter = StratifiedKFold(n_splits=self.n_splits, shuffle=True,
                                       random_state=self.random_state)
            splits = splitter.split(Xm, y)

        z_lr = np.zeros(n)
        z_gbm = np.zeros(n)
        z_mkt = np.full(n, np.nan)
        for tr, te in splits:
            sc = StandardScaler().fit(Xm[tr])
            lr = self._new_lr().fit(sc.transform(Xm[tr]), y[tr])
            z_lr[te] = _logit(lr.predict_proba(sc.transform(Xm[te]))[:, 1])
            if self.use_gbm:
                gbm = self._new_gbm().fit(Xdf.iloc[tr], y[tr])
                z_gbm[te] = _logit(gbm.predict_proba(Xdf.iloc[te])[:, 1])
            if market is not None and self.use_gbm:
                m_tr = tr[~np.isnan(market[tr])]
                if len(m_tr) >= 500:
                    mk = self._new_market().fit(
                        Xdf.iloc[m_tr], _logit(np.clip(market[m_tr], 0.01, 0.97)))
                    z_mkt[te] = mk.predict(Xdf.iloc[te])

        # ---- blend weights on honest OOF predictions
        self.w_gbm_ = self._best_blend(z_lr, z_gbm, y) if self.use_gbm else 0.0
        z_model = (1 - self.w_gbm_) * z_lr + self.w_gbm_ * z_gbm
        self.w_market_ = 0.0
        if market is not None and not np.isnan(z_mkt).all():
            zm = np.where(np.isnan(z_mkt), z_model, z_mkt)
            # cap at 0.6: the market may shape the curve, outcomes own the level
            self.w_market_ = self._best_blend(z_model, zm, y,
                                              grid=np.linspace(0, 0.6, 13))
            z_model = (1 - self.w_market_) * z_model + self.w_market_ * zm

        # ---- cross-fitted calibration
        p_oof = _sigmoid(z_model)
        self.calibrator_ = _Calibrator(self.calibration).fit(p_oof, y)
        self.calibrator_lr_ = _Calibrator(self.calibration).fit(_sigmoid(z_lr), y)

        # ---- refit everything on the full data for deployment
        self.scaler_ = StandardScaler().fit(Xm)
        self.lr_ = self._new_lr().fit(self.scaler_.transform(Xm), y)
        if self.use_gbm:
            self.gbm_ = self._new_gbm().fit(Xdf, y)
        if market is not None and self.use_gbm and self.w_market_ > 0:
            mask = ~np.isnan(market)
            self.market_ = self._new_market().fit(
                Xdf[mask], _logit(np.clip(market[mask], 0.01, 0.97)))

        # ---- level shifts so sum(xG) == goals, computed on calibrated
        # full-refit predictions: "_global" always (the full refit is slightly
        # sharper in-sample than the OOF preds the calibrator saw, so without
        # this the deployed sums drift ~1% high), plus one per league
        self.league_shifts_ = {}
        p_cal = self.calibrator_.apply(_sigmoid(self._raw_z(X)))
        self.league_shifts_["_global"] = round(self._solve_shift(p_cal, y.sum()), 6)
        if leagues is not None:
            leagues = np.asarray(leagues)
            for lg in np.unique(leagues):
                m = leagues == lg
                if m.sum() >= 200:
                    self.league_shifts_[str(lg)] = round(
                        self._solve_shift(p_cal[m], y[m].sum()), 6)

        # ---- honest metrics from the OOF predictions
        p_final = self.calibrator_.apply(p_oof)
        self.metrics_ = {
            "n_shots": int(n), "goal_rate": round(float(y.mean()), 4),
            "w_gbm": self.w_gbm_, "w_market": self.w_market_,
            "oof": {
                "brier": round(float(brier_score_loss(y, p_final)), 5),
                "log_loss": round(float(log_loss(y, np.clip(p_final, EPS, 1 - EPS))), 5),
                "roc_auc": round(float(roc_auc_score(y, p_final)), 4),
                "xg_over_goals": round(float(p_final.sum() / max(y.sum(), 1)), 4),
            },
            "oof_uncalibrated": {
                "brier": round(float(brier_score_loss(y, p_oof)), 5),
                "xg_over_goals": round(float(p_oof.sum() / max(y.sum(), 1)), 4),
            },
        }
        return self

    # ------------------------------------------------------------- prediction
    def _raw_z(self, X):
        Xdf = self._frame(X)
        z = _logit(self.lr_.predict_proba(
            self.scaler_.transform(Xdf.to_numpy()))[:, 1])
        if self.gbm_ is not None and self.w_gbm_ > 0:
            zg = _logit(self.gbm_.predict_proba(Xdf)[:, 1])
            z = (1 - self.w_gbm_) * z + self.w_gbm_ * zg
        if self.market_ is not None and self.w_market_ > 0:
            z = (1 - self.w_market_) * z + self.w_market_ * self.market_.predict(Xdf)
        return z

    def predict_proba(self, X):
        """sklearn-style (n, 2) calibrated probabilities."""
        p = self.calibrator_.apply(_sigmoid(self._raw_z(X)))
        p = np.clip(p, *CLIP)
        return np.column_stack([1 - p, p])

    def predict_xg(self, X, league=None):
        """Final per-shot xG (1-d array). Uses the league's level shift when
        known, else the global one."""
        p = self.predict_proba(X)[:, 1]
        shift = self.league_shifts_.get(str(league)) if league else None
        if shift is None:
            shift = self.league_shifts_.get("_global")
        if shift:
            p = np.clip(_sigmoid(_logit(p) + shift), *CLIP)
        return p

    # ------------------------------------------------------------ calibration
    def calibrate(self, X, y, leagues=None):
        """Re-fit ONLY the calibration map (and optional league shifts) on fresh
        held-out shots — e.g. when deploying to a new competition without
        retraining the base learners."""
        p_raw = _sigmoid(self._raw_z(X))
        y = np.asarray(y, int)
        self.calibrator_ = _Calibrator(self.calibration).fit(p_raw, y)
        p_cal = self.calibrator_.apply(p_raw)
        self.league_shifts_["_global"] = round(self._solve_shift(p_cal, y.sum()), 6)
        if leagues is not None:
            leagues = np.asarray(leagues)
            for lg in np.unique(leagues):
                m = leagues == lg
                if m.sum() >= 200:
                    self.league_shifts_[str(lg)] = round(
                        self._solve_shift(p_cal[m], y[m].sum()), 6)
        return self

    # ------------------------------------------------------------- evaluation
    def evaluate(self, X, y, league=None):
        y = np.asarray(y, int)
        p = self.predict_xg(X, league=league)
        return {
            "n": int(len(y)),
            "brier": round(float(brier_score_loss(y, p)), 5),
            "log_loss": round(float(log_loss(y, np.clip(p, EPS, 1 - EPS))), 5),
            "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "xg_sum": round(float(p.sum()), 2),
            "goals": int(y.sum()),
            "xg_over_goals": round(float(p.sum() / max(y.sum(), 1)), 4),
        }

    # ----------------------------------------------------------------- export
    def export(self, path, penalty_xg=0.76, extra_meta=None):
        """Write the JSON artifact consumed by score.XGScorer in every repo.
        LR coefficients are folded back to raw feature space so the runtime
        needs no scaler."""
        scale, mean = self.scaler_.scale_, self.scaler_.mean_
        coef_std = self.lr_.coef_[0]
        coef_raw = coef_std / scale
        intercept_raw = float(self.lr_.intercept_[0] - np.sum(coef_std * mean / scale))
        artifact = {
            "version": 2,
            "trained": datetime.date.today().isoformat(),
            "feature_names": FEATURE_NAMES,
            "lr": {"intercept": intercept_raw,
                   "coef": {f: float(c) for f, c in zip(FEATURE_NAMES, coef_raw)}},
            "gbm": (self.gbm_.booster_.model_to_string()
                    if self.gbm_ is not None else None),
            "market_distill": (self.market_.booster_.model_to_string()
                               if self.market_ is not None else None),
            "blend": {"w_gbm": self.w_gbm_, "w_market": self.w_market_},
            "calibrator": self.calibrator_.to_dict(),
            "calibrator_lr_only": self.calibrator_lr_.to_dict(),
            "league_shifts": self.league_shifts_,
            "penalty_xg": penalty_xg,
            "clip": list(CLIP),
            "metrics": self.metrics_,
            "meta": extra_meta or {},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(artifact, f)
        return artifact
