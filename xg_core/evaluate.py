"""Validation framework: proper-scoring metrics, reliability curves, and the
team-level bias report that proves (or disproves) the over-prediction is gone.

Per-shot metrics say whether probabilities are honest; the team/match
aggregation says whether the *sums* people actually look at track goals and the
market. A model can score a decent Brier and still run +15% hot at team level —
always check both.
"""
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

EPS = 1e-6


def summary_metrics(y, p, label=""):
    y = np.asarray(y, int)
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return {
        "model": label, "n": int(len(y)),
        "brier": round(float(brier_score_loss(y, p)), 5),
        "log_loss": round(float(log_loss(y, p)), 5),
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "xg_sum": round(float(p.sum()), 1),
        "goals": int(y.sum()),
        "bias_pct": round(float(100.0 * (p.sum() / max(y.sum(), 1) - 1.0)), 2),
    }


def calibration_table(y, p, bins=10):
    """Reliability by predicted-probability decile. A calibrated model has
    mean_xg ~= goal_rate in every row; the old model's failure mode shows up as
    mean_xg > goal_rate concentrated in the low buckets."""
    df = pd.DataFrame({"y": np.asarray(y, int), "p": np.asarray(p, float)})
    df["bucket"] = pd.qcut(df["p"], bins, duplicates="drop")
    out = (df.groupby("bucket", observed=True)
             .agg(shots=("y", "size"), mean_xg=("p", "mean"),
                  goal_rate=("y", "mean"))
             .reset_index())
    out["gap"] = out["mean_xg"] - out["goal_rate"]
    return out.round(4)


def expected_calibration_error(y, p, bins=10):
    t = calibration_table(y, p, bins)
    return round(float((t["shots"] * t["gap"].abs()).sum() / t["shots"].sum()), 5)


def team_match_bias(shots_df, pred_col="xg", market_col=None):
    """Aggregate to (match, team): the unit pundits and dashboards consume.

    shots_df needs: match_id, team_id, y, <pred_col> [, market_col].
    Returns (per_team, overall) where per_team has one row per team with the
    season totals and bias, and overall has league-level bias plus per-match
    agreement (MAE/correlation) against the market when supplied.
    """
    g = shots_df.groupby(["match_id", "team_id"], as_index=False).agg(
        shots=("y", "size"), goals=("y", "sum"), xg=(pred_col, "sum"),
        **({"market": (market_col, "sum")} if market_col else {}))

    per_team = g.groupby("team_id", as_index=False).agg(
        matches=("match_id", "nunique"), shots=("shots", "sum"),
        goals=("goals", "sum"), xg=("xg", "sum"),
        **({"market": ("market", "sum")} if market_col else {}))
    per_team["xg_minus_goals"] = per_team["xg"] - per_team["goals"]
    per_team["xg_over_goals"] = per_team["xg"] / per_team["goals"].clip(lower=1)
    if market_col:
        per_team["xg_over_market"] = per_team["xg"] / per_team["market"].clip(lower=0.1)
    per_team = per_team.sort_values("xg_minus_goals", ascending=False).round(2)

    overall = {
        "team_matches": int(len(g)),
        "total_goals": int(g["goals"].sum()),
        "total_xg": round(float(g["xg"].sum()), 1),
        "bias_pct": round(float(100.0 * (g["xg"].sum() / max(g["goals"].sum(), 1) - 1)), 2),
        "teams_overpredicted": int((per_team["xg_minus_goals"] > 0).sum()),
        "teams_total": int(len(per_team)),
    }
    if market_col:
        overall.update({
            "vs_market_mae_per_teammatch": round(
                float((g["xg"] - g["market"]).abs().mean()), 3),
            "vs_market_corr": round(float(g["xg"].corr(g["market"])), 4),
            "vs_market_bias_pct": round(
                float(100.0 * (g["xg"].sum() / max(g["market"].sum(), 0.1) - 1)), 2),
        })
    return per_team, overall


def print_report(y, preds_by_name, shots_df=None, market_col=None):
    """preds_by_name: {"old model": p_old, "calibrated blend": p_new, ...}"""
    rows = [summary_metrics(y, p, label) for label, p in preds_by_name.items()]
    print("\n== per-shot metrics ==")
    print(pd.DataFrame(rows).to_string(index=False))
    last_label, last_p = list(preds_by_name.items())[-1]
    print(f"\n== reliability ({last_label}) ==   ECE="
          f"{expected_calibration_error(y, last_p)}")
    print(calibration_table(y, last_p).to_string(index=False))
    if shots_df is not None:
        per_team, overall = team_match_bias(shots_df, market_col=market_col)
        print("\n== team-level bias (worst overpredictions first) ==")
        print(per_team.head(12).to_string(index=False))
        print("\n== league level ==")
        for k, v in overall.items():
            print(f"  {k}: {v}")
