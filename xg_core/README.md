# xg_core — calibrated xG + xA models for XLALIGA · XWORLDCUP · BCNPROJECT-main

Trained artifacts (`xg_artifact.json`, `xa_artifact.json`), one scoring API each,
synced as files. Updating a model = retrain once, copy the folder to the other
repos. No repo ever hard-codes coefficients again.

```
xg_core/
  features.py       shot event -> features (stdlib only; single source of truth for geometry)
  score.py          XGScorer: pure-python runtime scoring of the artifact (stdlib only)
  xg_artifact.json  the trained xG model (LR coefs + LightGBM boosters + calibration map)
  model.py          CalibratedXGModel: training pipeline (numpy/pandas/sklearn, lightgbm opt.)
  evaluate.py       Brier/log-loss/AUC, reliability tables, team-level bias report
  train.py          CLI: raw WhoScored JSONs -> fitted, validated, exported artifact
  xa_features.py    pass event -> features (stdlib only; shares geometry with features.py)
  xa_score.py       XAScorer: pure-python runtime xA scoring (stdlib only)
  xa_artifact.json  the trained pass-level xA model
  xa_model.py       CalibratedXAModel: two-stage xA training pipeline
  train_xa.py       CLI: raw WhoScored JSONs -> fitted, validated xA artifact
```

## Scoring (what the dashboards/renderers call)

```python
from xg_core import XGScorer
scorer = XGScorer()  # loads xg_core/xg_artifact.json

# drop-in for the old estimate_xg(...) signature:
xg = scorer.estimate_xg(x_sb, y_sb, is_penalty, big_chance, body_part,
                        situation, assisted=True, league="LaLiga")
```

Runtime needs **stdlib only** (vendor `features.py` + `score.py` + the artifact if you
don't want the package). If `lightgbm` is importable, the scorer silently upgrades from
the calibrated-logistic path to the full LR+GBM blend — both paths carry their own
calibration map, so both are calibrated.

Integration per repo = route the existing model through the scorer, e.g. in
`laliga_dashboard/xg_model.py` (and mirror in `renderer._estimate_xg`):

```python
from xg_core.score import XGScorer
_SCORER = XGScorer()

def estimate_xg(x_sb, y_sb, is_penalty, is_big_chance, body_part, situation="Open Play"):
    return _SCORER.estimate_xg(x_sb, y_sb, is_penalty, is_big_chance,
                               body_part, situation, league="LaLiga")
```

then rebuild the derived data (`build_match_details.py`, `build_players.py`,
`build_shots.py`, `build_data.py`) so the site picks up the new values.

## Training / retraining

```powershell
py -m xg_core.train `
    --shots "LaLiga=..\XWORLDCUPTWIT\laliga\matches\2025-26" `
    --shots "WorldCup=..\XWORLDCUPTWIT\wc2026\matches" `
    --market market_xg.csv `
    --out xg_core\xg_artifact.json
```

- `--shots LEAGUE=DIR`, repeatable — every corpus you have; one model, per-league level
  shifts on top (a shift is fitted for any league with ≥200 shots; `_global` covers the rest).
- `--market` (optional) CSV `match_id,event_id,market_xg` with Understat/FotMob/Opta xG for
  any subset of shots. Used for **distillation**: a regressor learns to predict the market's
  xG *from our features*, and its logit is blended in with a weight chosen by out-of-fold
  Brier against real goals (capped at 0.6 — the market shapes the curve, outcomes own the
  level). Market numbers are **never input features**: they don't exist for tomorrow's
  shots, so a model that eats them can't score live matches.
- `--no-gbm` for a strictly dependency-free logistic-only artifact.

## Design decisions (why the bias won't come back)

| Decision | Rationale |
|---|---|
| No resampling / class weights for the 10% goal rate | Reweighting shifts the base rate → systematically inflated probabilities. Log-loss on the natural distribution is the correct objective for a probability. |
| Cross-fitted calibration (isotonic on out-of-fold preds; Platt below 100 positives) | Calibrating on in-sample predictions is how optimism survives. `calibration="platt"` available on `CalibratedXGModel`. |
| Monotone constraints on the GBM (dist ↓, angle ↑, big_chance ↑) | The tree model cannot learn upward-sloping pockets that overprice bad shots. |
| `log_dist` + `dist×angle`, `big×dist`, `header×dist` interactions | The old model's distance coefficient was ‑0.004 (≈0): a 40 m punt priced like a 26 m drive, and the BigChance bonus applied full-strength anywhere. |
| Clip floor 0.002, not 0.01 | A 0.01 floor alone banks ~0.25 xG per match of speculative shots. |
| Per-league logit level shifts, solved on calibrated predictions | Replaces the old single `_CAL_SHIFT`; sums match goals per competition without warping the curve shape. |
| Penalties = empirical constant (0.777 from 148 kicks) | Never modelled; excluded from training. |
| Grouped CV by match | Shots from one match share rebounds/keeper/context; letting them straddle folds leaks. |

## Current artifact (trained 2026-07-02, La Liga 25/26 + WC 2026)

11,848 non-penalty shots (own-goal events excluded — 40 of the corpus' "goals" were
own goals at junk coordinates, a label bug that inflated the long tail), 1,168 goals,
market-distilled against 9,453 Understat per-shot xG values
(`market_understat_laliga_2025-26.csv`, w_market = 0.10 chosen by OOF Brier).
Out-of-fold: **Brier 0.0718 · log-loss 0.2526 · AUC 0.811 · ΣxG/goals = 1.000**.

### Benchmark comparison (2026-07-02)

Team-match level, all 380 La Liga matches vs **Understat** and 18 WC matches vs
**FotMob official**:

| | vs Understat (760 team-matches) | vs FotMob (36) |
|---|---|---|
| old hard-coded model | MAE 0.238 · r 0.959 · level −12.5% | MAE 0.355 · r 0.981 · −23.1% |
| **this artifact** | MAE 0.236 · r 0.955 · level −12.1% | MAE 0.197 · r 0.973 · −8.0% |

**The −12% level gap vs Understat is theirs, not ours.** Understat sums run above
actual goals in every league-season checked: La Liga 23/24 +8.7%, 24/25 +8.8%,
25/26 +11.4%, EPL 24/25 +9.1%, 25/26 +11.2%. Per-segment on 9,453 identical shots,
actual goal rates side with this model everywhere:

| segment | actual | Understat | this model |
|---|---|---|---|
| >25 m | 3.3% | 2.8% | **3.3%** |
| 11–18 m | 10.1% | 12.7% | **10.5%** |
| ≤11 m | 21.0% | 27.1% | **21.4%** |
| headers | 9.2% | 12.6% | **9.1%** |
| big chances | 34.9% | 40.0% | **34.7%** |

If you ever *want* market-level output (numbers comparable to Understat dashboards),
add ~+0.24 to the logit — but the calibrated level is the honest probability.

Understat's new AJAX endpoints (their old `JSON.parse` blobs are gone):
`GET /getLeagueData/<league>/<startYear>` (league page: all matches + team histories +
player xG) and `GET /getMatchData/<id>` (per-shot xG + rosters). Both bot-blocked for
raw HTTP — fetch through a Selenium page context with `X-Requested-With: XMLHttpRequest`.

## xA — pass-level expected assists (`xa_artifact.json`)

The dashboards' old xA credited a passer with the xG of the shot that followed —
so a perfect through-ball the striker never shot from earned 0, and the metric's
sum ran ~11% above real assists (shot xG measures "goal from this shot", not
"assist credited to this pass"). The xA model scores the PASS itself:

**xA(pass) = calibrated P(this successful pass becomes a goal assist)**, summed
over every successful pass a player plays. Failed passes score 0 by definition.

Design (see `xa_model.py` docstring for the full rationale):
- **Two stages**, because assists are 0.19% of passes but shots-from-passes are
  2.2%: stage A = P(shot follows | pass) (LR + monotone-GBM blend, weight by OOF
  Brier); stage B = E[logit v2-xG of the resulting shot | pass features],
  distilled from the linked key passes (the xG model's market trick).
- The **product is isotonic-calibrated, cross-fitted,** against real
  `IntentionalGoalAssist` labels — the same definition the dashboards count in
  `a`, deliberately not "the linked shot scored" (812 vs 673: the diff is
  deflected scrambles Opta refuses to credit).
- **No reweighting** of the base rate, grouped CV by match, per-league level
  shifts, per-pass clip at 0.6 (the isotonic tail above raw 0.64 rests on a
  handful of extreme passes — the clip is the guard rail).
- Features: end-location goal geometry (dist/angle, shared with the shot model),
  origin distance + progression, length, into-box/six flags, and type flags
  (cross, through-ball, derived cutback, chipped, long ball, lay-off, head pass,
  corner, free kick, throw-in).

### Current artifact (trained 2026-07-03, La Liga 25/26 + WC 2026)

363,324 successful passes (462 matches), 8,043 shot-producing, 661 assists.
Out-of-fold: **Brier 0.00153 · AUC 0.992 · ΣxA/assists = 1.000** (stage A alone:
AUC 0.966). Deployed sums: La Liga 532.8 xA vs 535 assists, WC 125.3 vs 126.

Player-level (600 La Liga players): corr(xA, assists) 0.836 — level with the old
shot-based method (0.836) on correlation, but the old method's sum ran 601.8 vs
542 actual assists (+11%) while the new one is level; per-player MAE improves
0.506 -> 0.497. The gains concentrate exactly where the old method was blind:
creators whose passes don't get shot from (e.g. wing-backs' cutback/cross volume)
finally register, and inflated shot-xG credit is deflated to honest assist odds.

### Retraining

```powershell
py -m xg_core.train_xa `
    --passes "LaLiga=..\XWORLDCUPTWIT\laliga\matches\2025-26" `
    --passes "WorldCup=..\XWORLDCUPTWIT\wc2026\matches" `
    --out xg_core\xa_artifact.json
```

Stage B needs `xg_artifact.json` present (targets are v2 xG values). Ship when:
OOF Brier/AUC don't regress, ΣxA/assists ≈ 1.00 overall and per league, and the
top of the player table still looks like the league's actual creators.

### Integration

```python
from xg_core.xa_score import XAScorer
_XA = XAScorer()                        # stdlib only; lightgbm upgrades it silently
xa_map = _XA.player_xa_from_events(match_data, league="LaLiga")  # playerId -> xA
```

Drop-in for `xg_model.player_xa_from_events` in `build_players.py` (~0.1 s per
match with lightgbm). Vendor `xa_features.py` + `xa_score.py` + `features.py` +
`xa_artifact.json` if you don't want the package.

## Validation checklist for any retrain

`train.py` prints all of these — a retrain is shippable when:
1. OOF Brier / log-loss / AUC don't regress.
2. Reliability table: |gap| small in **every** bucket, no monotone run of positive gaps.
3. `ΣxG / goals ≈ 1.00` overall and per league.
4. Team-level report: over-predicted teams ≈ half the teams (a coin flip), not 80%+.
   Per-team xG ≠ goals is *signal* (finishing over/under-performance), not bias —
   bias is when nearly every team lands on the same side.
5. With `--market`: per-team-match MAE and correlation vs the market; expect corr ≥ 0.8.
