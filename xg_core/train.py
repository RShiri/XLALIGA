"""Fit, validate and export the shared xG model from raw WhoScored match JSONs.

Usage (from the repo root; point at every corpus you have — one model for all
three projects, per-league level shifts on top):

  py -m xg_core.train ^
      --shots "LaLiga=../XWORLDCUPTWIT/laliga/matches/2025-26" ^
      --shots "WorldCup=../XWORLDCUPTWIT/matches" ^
      --market market_xg.csv ^
      --out xg_core/xg_artifact.json

--market (optional) is a CSV with columns match_id,event_id,market_xg holding
per-shot xG from Understat/FotMob/Opta for whatever subset you have; it anchors
the model's shape via distillation. Coverage can be partial.
"""
import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

from .evaluate import print_report
from .features import FEATURE_NAMES, iter_shots
from .model import CalibratedXGModel


def load_shot_table(specs):
    rows = []
    for spec in specs:
        league, _, folder = spec.partition("=")
        if not folder:
            league, folder = os.path.basename(spec.rstrip("/\\")), spec
        files = sorted(glob.glob(os.path.join(folder, "*.json")))
        if not files:
            raise SystemExit(f"no match JSONs found in {folder}")
        for path in files:
            with open(path, encoding="utf-8") as f:
                match = json.load(f)
            mid = os.path.splitext(os.path.basename(path))[0]
            rows.extend(iter_shots(match, league=league, match_id=mid))
        print(f"  {league}: {len(files)} matches -> {len(rows)} shots so far")
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shots", action="append", required=True,
                    metavar="LEAGUE=DIR", help="raw WhoScored JSON folder(s)")
    ap.add_argument("--market", help="CSV: match_id,event_id,market_xg")
    ap.add_argument("--out", default="xg_core/xg_artifact.json")
    ap.add_argument("--no-gbm", action="store_true",
                    help="logistic-only (for a strictly dependency-free runtime)")
    args = ap.parse_args()

    df = load_shot_table(args.shots)
    pens = df[df["penalty"]]
    df = df[~df["penalty"]].reset_index(drop=True)
    print(f"\ntraining on {len(df)} non-penalty shots, {df['y'].sum()} goals "
          f"({df['y'].mean():.3f} goal rate), {df['match_id'].nunique()} matches")

    # penalties: scored as a constant = empirical conversion (fallback 0.76)
    penalty_xg = round(float(pens["y"].mean()), 3) if len(pens) >= 50 else 0.76
    print(f"penalties: {len(pens)} kicks -> penalty_xg={penalty_xg}")

    market = None
    if args.market:
        mk = pd.read_csv(args.market, dtype={"match_id": str})
        df = df.merge(mk, on=["match_id", "event_id"], how="left")
        market = df["market_xg"]
        print(f"market anchor: {market.notna().sum()}/{len(df)} shots covered")

    model = CalibratedXGModel(use_gbm=not args.no_gbm)
    model.fit(df[FEATURE_NAMES], df["y"], groups=df["match_id"],
              market_xg=market, leagues=df["league"])
    print(f"\nblend weights: gbm={model.w_gbm_}, market={model.w_market_}")
    print(f"league shifts: {model.league_shifts_}")
    print(f"OOF metrics: {model.metrics_['oof']}")

    # full validation report on OOF-honest final predictions vs the old model
    df["xg"] = model.predict_xg(df[FEATURE_NAMES])
    preds = {"new (calibrated blend)": df["xg"].to_numpy()}
    print_report(df["y"], preds, shots_df=df,
                 market_col="market_xg" if market is not None else None)

    model.export(args.out, penalty_xg=penalty_xg,
                 extra_meta={"sources": args.shots,
                             "market_csv": args.market or ""})
    print(f"\nartifact written -> {args.out}")
    print("sync: copy xg_core/ (or just features.py + score.py + the artifact) "
          "into XWORLDCUP / XLALIGA / BCNPROJECT-main and route "
          "estimate_xg() through score.XGScorer")


if __name__ == "__main__":
    main()
