"""Fit, validate and export the pass-level xA model from raw WhoScored JSONs.

Usage (from the XLALIGA repo root):

  py -m xg_core.train_xa ^
      --passes "LaLiga=../XWORLDCUPTWIT/laliga/matches/2025-26" ^
      --passes "WorldCup=../XWORLDCUPTWIT/wc2026/matches" ^
      --out xg_core/xa_artifact.json

Stage B distils the v2 xG model (xg_artifact.json must exist): each linked
key pass's target is the v2 xG of the shot it created.
"""
import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

from .score import XGScorer
from .xa_features import PASS_FEATURE_NAMES, iter_passes
from .xa_model import CalibratedXAModel


def load_pass_table(specs, xg_scorer):
    rows = []
    for spec in specs:
        league, _, folder = spec.partition("=")
        if not folder:
            league, folder = os.path.basename(spec.rstrip("/\\")), spec
        files = sorted(glob.glob(os.path.join(folder, "*.json")))
        files = [f for f in files if "cache" not in os.path.basename(f)]
        if not files:
            raise SystemExit(f"no match JSONs found in {folder}")
        n0 = len(rows)
        for path in files:
            try:
                with open(path, encoding="utf-8") as f:
                    match = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if not match.get("events"):
                continue  # unplayed knockout stubs
            mid = os.path.splitext(os.path.basename(path))[0]
            rows.extend(iter_passes(match, league=league, match_id=mid,
                                    xg_scorer=xg_scorer))
        print(f"  {league}: {len(files)} files -> {len(rows) - n0} passes")
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--passes", action="append", required=True,
                    metavar="LEAGUE=DIR", help="raw WhoScored JSON folder(s)")
    ap.add_argument("--out", default="xg_core/xa_artifact.json")
    ap.add_argument("--no-gbm", action="store_true",
                    help="linear-only (strictly dependency-free runtime)")
    args = ap.parse_args()

    xg_scorer = XGScorer()  # stage-B targets come from the v2 xG artifact
    df = load_pass_table(args.passes, xg_scorer)
    print(f"\ntraining on {len(df)} successful passes: "
          f"{df['shot_followed'].sum()} led to a shot "
          f"({df['xg_target'].notna().sum()} with resolved xG), "
          f"{df['y'].sum()} assists, {df['match_id'].nunique()} matches")

    model = CalibratedXAModel(use_gbm=not args.no_gbm)
    model.fit(df[PASS_FEATURE_NAMES], df["shot_followed"], df["y"],
              df["xg_target"], groups=df["match_id"], leagues=df["league"])
    print(f"\nstage-A blend: w_gbm={model.w_gbm_}")
    print(f"league shifts: {model.league_shifts_}")
    print(f"stage-A OOF: {model.metrics_['stage_a_oof']}")
    print(f"OOF metrics: {model.metrics_['oof']}")

    # per-league sums on final deployed predictions
    for lg in df["league"].unique():
        m = df["league"] == lg
        xa = model.predict_xa(df[m][PASS_FEATURE_NAMES], league=lg)
        print(f"  {lg}: sum xA={xa.sum():.1f} vs assists={df[m]['y'].sum()} "
              f"(ratio {xa.sum() / max(df[m]['y'].sum(), 1):.3f})")

    model.export(args.out, extra_meta={"sources": args.passes})
    print(f"\nartifact written -> {args.out}")
    print("integrate: build_players.py xa <- xa_score.XAScorer()"
          ".player_xa_from_events(match_data, league=...)")


if __name__ == "__main__":
    main()
