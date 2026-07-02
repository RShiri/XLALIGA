"""CalibratedXAModel — pass-level expected assists, trained the xg_core way.

Why a pass model at all: the dashboards' current xA credits a passer with the
xG of the shot that followed — so a perfect through-ball the striker never
gets a shot from earns exactly 0, and a hopeful sideways nudge before a
30-metre screamer earns the screamer's xG. Modelling the PASS fixes both:
xA(pass) = calibrated P(this pass becomes an assist), summed over every
successful pass a player attempts.

Design (deliberately parallel to model.CalibratedXGModel):

* TWO STAGES, because assists are rare (673 in 363k passes, 0.19%) but the
  intermediate signal is not: 8k passes produced a shot. Stage A learns
  P(shot follows | pass) as an LR + monotone-GBM logit blend (weight chosen
  on out-of-fold Brier). Stage B learns E[logit xG of the resulting shot |
  pass features] on the linked key passes — the same distillation trick the
  xG model uses for market anchoring: the target only exists for historical
  passes, so a student regressor makes it available at inference forever.
  Their product P(shot) x E[conversion] is the raw expected-assist rate.

* The product is then CALIBRATED, cross-fitted, against the real assist
  labels (IntentionalGoalAssist — the same definition the dashboards count):
  isotonic on out-of-fold products. This absorbs both the stages' correlation
  (good passes raise shot odds AND shot quality) and the gap between "the
  linked shot scored" (812) and "Opta credited an assist" (673).

* Class imbalance is handled by DOING NOTHING, exactly like the shot model:
  no reweighting, no resampling. 0.19% is the true base rate of a pass
  becoming an assist; a model that inflates it is lying about probabilities.

* Grouped CV by match: passes from one match share game state, keeper form
  and the same handful of shots; letting them straddle folds leaks.

* Per-league logit shifts on the calibrated output so sum(xA) == assists in
  each competition, without warping the curve shape.

Everything exports to xa_artifact.json scored by xa_score.XAScorer (pure
python; LightGBM optional), so the dashboards keep their no-dependency builds.
"""
import datetime
import json
import warnings

import numpy as np
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold, StratifiedKFold
from sklearn.preprocessing import StandardScaler

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

from .model import _Calibrator, _logit, _sigmoid
from .xa_features import PASS_FEATURE_NAMES, PASS_MONOTONE

EPS = 1e-6
XA_CLIP = (0.0, 0.6)   # floor 0 is honest — a back pass genuinely cannot assist
XG_TARGET_CLIP = (0.01, 0.97)


class CalibratedXAModel:
    """fit / predict_xa / evaluate / export for pass-level expected assists."""

    def __init__(self, calibration="isotonic", n_splits=5, use_gbm=True,
                 random_state=42):
        self.calibration = calibration
        self.n_splits = n_splits
        self.use_gbm = use_gbm and HAS_LGBM
        if use_gbm and not HAS_LGBM:
            warnings.warn("lightgbm not installed — falling back to linear-only")
        self.random_state = random_state
        self.scaler_ = None
        self.lr_shot_ = None          # stage A linear path
        self.gbm_shot_ = None         # stage A tree path
        self.w_gbm_ = 0.0
        self.linreg_xg_ = None        # stage B linear fallback (pure-python path)
        self.gbm_xg_ = None           # stage B tree regressor
        self.calibrator_ = _Calibrator(calibration)
        self.calibrator_lr_ = _Calibrator(calibration)
        self.league_shifts_ = {}
        self.metrics_ = {}

    # ---------------------------------------------------------------- helpers
    def _frame(self, X):
        return X[PASS_FEATURE_NAMES].astype(float)

    def _new_lr(self):
        return LogisticRegression(C=1.0, max_iter=5000)

    def _new_gbm_shot(self):
        return lgb.LGBMClassifier(
            objective="binary",
            n_estimators=600, learning_rate=0.05,
            num_leaves=31, min_child_samples=200,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            reg_lambda=5.0,
            monotone_constraints=[PASS_MONOTONE.get(f, 0)
                                  for f in PASS_FEATURE_NAMES],
            # NO is_unbalance / scale_pos_weight — see module docstring
            random_state=self.random_state, verbose=-1,
        )

    def _new_gbm_xg(self):
        return lgb.LGBMRegressor(
            n_estimators=400, learning_rate=0.05, num_leaves=15,
            min_child_samples=80, subsample=0.8, subsample_freq=1,
            reg_lambda=5.0,
            monotone_constraints=[PASS_MONOTONE.get(f, 0)
                                  for f in PASS_FEATURE_NAMES],
            random_state=self.random_state, verbose=-1,
        )

    @staticmethod
    def _best_blend(z_a, z_b, y, grid=None):
        best_w, best_brier = 0.0, np.inf
        for w in (grid if grid is not None else np.linspace(0, 1, 21)):
            b = brier_score_loss(y, _sigmoid((1 - w) * z_a + w * z_b))
            if b < best_brier:
                best_w, best_brier = float(w), b
        return best_w

    @staticmethod
    def _solve_shift(p, total, lo=-2.0, hi=2.0):
        """Logit shift d with sum(sigmoid(logit(p)+d)) == total (bisection).
        Zeros stay zero — logit clips them to EPS, sigmoid maps back ~0."""
        z = _logit(np.clip(p, EPS, 1 - EPS))
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if _sigmoid(z + mid).sum() < total:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    # -------------------------------------------------------------------- fit
    def fit(self, X, y_shot, y_assist, xg_target, groups=None, leagues=None):
        """X: DataFrame with PASS_FEATURE_NAMES columns. y_shot: a shot
        followed. y_assist: IntentionalGoalAssist. xg_target: v2 xG of the
        linked shot (NaN when unlinked). groups: match ids."""
        Xdf = self._frame(X)
        Xm = Xdf.to_numpy()
        y_shot = np.asarray(y_shot, int)
        y_assist = np.asarray(y_assist, int)
        xg_t = np.asarray(xg_target, float)
        n = len(y_assist)
        z_t = np.where(np.isnan(xg_t), np.nan,
                       _logit(np.clip(xg_t, *XG_TARGET_CLIP)))

        if groups is not None:
            splits = GroupKFold(n_splits=self.n_splits).split(
                Xm, y_assist, np.asarray(groups))
        else:
            splits = StratifiedKFold(
                n_splits=self.n_splits, shuffle=True,
                random_state=self.random_state).split(Xm, y_assist)

        # ---- out-of-fold predictions for both stages
        zA_lr = np.zeros(n)
        zA_gbm = np.zeros(n)
        zB_lin = np.zeros(n)
        zB_gbm = np.zeros(n)
        for tr, te in splits:
            sc = StandardScaler().fit(Xm[tr])
            lr = self._new_lr().fit(sc.transform(Xm[tr]), y_shot[tr])
            zA_lr[te] = _logit(lr.predict_proba(sc.transform(Xm[te]))[:, 1])
            b_tr = tr[~np.isnan(z_t[tr])]
            linr = LinearRegression().fit(Xm[b_tr], z_t[b_tr])
            zB_lin[te] = linr.predict(Xm[te])
            if self.use_gbm:
                g = self._new_gbm_shot().fit(Xdf.iloc[tr], y_shot[tr])
                zA_gbm[te] = _logit(g.predict_proba(Xdf.iloc[te])[:, 1])
                r = self._new_gbm_xg().fit(Xdf.iloc[b_tr], z_t[b_tr])
                zB_gbm[te] = r.predict(Xdf.iloc[te])

        # ---- stage-A blend weight on honest OOF predictions
        self.w_gbm_ = (self._best_blend(zA_lr, zA_gbm, y_shot)
                       if self.use_gbm else 0.0)
        zA = (1 - self.w_gbm_) * zA_lr + self.w_gbm_ * zA_gbm
        zB = zB_gbm if self.use_gbm else zB_lin

        # ---- cross-fitted calibration of the product against real assists
        p_raw = _sigmoid(zA) * _sigmoid(zB)
        self.calibrator_ = _Calibrator(self.calibration).fit(p_raw, y_assist)
        p_raw_lr = _sigmoid(zA_lr) * _sigmoid(zB_lin)
        self.calibrator_lr_ = _Calibrator(self.calibration).fit(p_raw_lr, y_assist)

        # ---- refit everything on the full data for deployment
        self.scaler_ = StandardScaler().fit(Xm)
        self.lr_shot_ = self._new_lr().fit(self.scaler_.transform(Xm), y_shot)
        mask_b = ~np.isnan(z_t)
        self.linreg_xg_ = LinearRegression().fit(Xm[mask_b], z_t[mask_b])
        if self.use_gbm:
            self.gbm_shot_ = self._new_gbm_shot().fit(Xdf, y_shot)
            self.gbm_xg_ = self._new_gbm_xg().fit(Xdf[mask_b], z_t[mask_b])

        # ---- league level shifts on calibrated full-refit predictions
        self.league_shifts_ = {}
        p_cal = self.calibrator_.apply(self._raw_p(X))
        self.league_shifts_["_global"] = round(
            self._solve_shift(p_cal, y_assist.sum()), 6)
        if leagues is not None:
            leagues = np.asarray(leagues)
            for lg in np.unique(leagues):
                m = leagues == lg
                if y_assist[m].sum() >= 30:
                    self.league_shifts_[str(lg)] = round(
                        self._solve_shift(p_cal[m], y_assist[m].sum()), 6)

        # ---- honest metrics from the OOF predictions
        p_final = self.calibrator_.apply(p_raw)
        pc = np.clip(p_final, EPS, 1 - EPS)
        self.metrics_ = {
            "n_passes": int(n),
            "assist_rate": round(float(y_assist.mean()), 5),
            "shot_rate": round(float(y_shot.mean()), 4),
            "w_gbm": self.w_gbm_,
            "stage_a_oof": {
                "brier": round(float(brier_score_loss(y_shot, _sigmoid(zA))), 5),
                "roc_auc": round(float(roc_auc_score(y_shot, zA)), 4),
            },
            "oof": {
                "brier": round(float(brier_score_loss(y_assist, p_final)), 6),
                "log_loss": round(float(log_loss(y_assist, pc)), 6),
                "roc_auc": round(float(roc_auc_score(y_assist, p_final)), 4),
                "xa_over_assists": round(
                    float(p_final.sum() / max(y_assist.sum(), 1)), 4),
            },
            "oof_uncalibrated": {
                "brier": round(float(brier_score_loss(y_assist, p_raw)), 6),
                "xa_over_assists": round(
                    float(p_raw.sum() / max(y_assist.sum(), 1)), 4),
            },
        }
        return self

    # ------------------------------------------------------------- prediction
    def _raw_p(self, X):
        Xdf = self._frame(X)
        Xm = Xdf.to_numpy()
        zA = _logit(self.lr_shot_.predict_proba(
            self.scaler_.transform(Xm))[:, 1])
        if self.gbm_shot_ is not None and self.w_gbm_ > 0:
            zg = _logit(self.gbm_shot_.predict_proba(Xdf)[:, 1])
            zA = (1 - self.w_gbm_) * zA + self.w_gbm_ * zg
        zB = (self.gbm_xg_.predict(Xdf) if self.gbm_xg_ is not None
              else self.linreg_xg_.predict(Xm))
        return _sigmoid(zA) * _sigmoid(zB)

    def predict_xa(self, X, league=None):
        p = self.calibrator_.apply(self._raw_p(X))
        shift = self.league_shifts_.get(str(league)) if league else None
        if shift is None:
            shift = self.league_shifts_.get("_global")
        if shift:
            nz = p > 0
            p = np.where(nz, _sigmoid(_logit(np.clip(p, EPS, 1 - EPS)) + shift), 0.0)
        return np.clip(p, *XA_CLIP)

    # ------------------------------------------------------------- evaluation
    def evaluate(self, X, y_assist, league=None):
        y = np.asarray(y_assist, int)
        p = self.predict_xa(X, league=league)
        return {
            "n": int(len(y)),
            "brier": round(float(brier_score_loss(y, p)), 6),
            "log_loss": round(float(log_loss(y, np.clip(p, EPS, 1 - EPS))), 6),
            "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "xa_sum": round(float(p.sum()), 2),
            "assists": int(y.sum()),
            "xa_over_assists": round(float(p.sum() / max(y.sum(), 1)), 4),
        }

    # ----------------------------------------------------------------- export
    def export(self, path, extra_meta=None):
        """Write xa_artifact.json for xa_score.XAScorer. LR coefficients are
        folded back to raw feature space so the runtime needs no scaler."""
        scale, mean = self.scaler_.scale_, self.scaler_.mean_
        coef_std = self.lr_shot_.coef_[0]
        coef_raw = coef_std / scale
        intercept_raw = float(self.lr_shot_.intercept_[0]
                              - np.sum(coef_std * mean / scale))
        artifact = {
            "version": 1,
            "kind": "xa_pass_model",
            "trained": datetime.date.today().isoformat(),
            "feature_names": PASS_FEATURE_NAMES,
            "shot_lr": {"intercept": intercept_raw,
                        "coef": {f: float(c) for f, c
                                 in zip(PASS_FEATURE_NAMES, coef_raw)}},
            "shot_gbm": (self.gbm_shot_.booster_.model_to_string()
                         if self.gbm_shot_ is not None else None),
            "xg_linreg": {"intercept": float(self.linreg_xg_.intercept_),
                          "coef": {f: float(c) for f, c in zip(
                              PASS_FEATURE_NAMES, self.linreg_xg_.coef_)}},
            "xg_gbm": (self.gbm_xg_.booster_.model_to_string()
                       if self.gbm_xg_ is not None else None),
            "blend": {"w_gbm": self.w_gbm_},
            "calibrator": self.calibrator_.to_dict(),
            "calibrator_lr_only": self.calibrator_lr_.to_dict(),
            "league_shifts": self.league_shifts_,
            "clip": list(XA_CLIP),
            "metrics": self.metrics_,
            "meta": extra_meta or {},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(artifact, f)
        return artifact
