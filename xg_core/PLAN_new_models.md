# Plan: new models for xg_core (xGOT, goals-prevented, progressive actions, PPDA, xT)

**Status: planning only — nothing in this doc is built yet.** Written 2026-09-14 for a
future session to execute. `xg_core/` stays the canonical home (elevated in place, not
split into a new repo) — same pattern as today: build here, vendor copies into
`XWORLDCUPTWIT` and `BCNPROJECT-main` when a model updates, per `xg_core/README.md`.

**Correction, added later the same day**: this doc assumes shot-outcome models train
inside this `xg_core/` folder. That's true for the *old* xg_core (v2)/xA, but the
*live* shot-xG model (`xg_core_v3`) actually trains in a separate standalone repo,
`C:\Users\puzik\XG V3` (see `UPDATE_XG_CORE_PROMPT.md` at the XLALIGA repo root). A new
shot-outcome model like xGOT should very likely train there too, reusing its existing
corpus/retrain pipeline, not here. See `PROMPT_NEW_MODELS.md` (XLALIGA repo root) for
the corrected, actionable version of this plan.

## Why these five, in this order

Ordered by how much new work each needs — cheapest, most-validated wins first, biggest
research lift last. Do them roughly in this sequence; each is independently shippable
(nothing here blocks on xT, which is easily the largest item).

1. **xGOT** (expected goals *on target*) — almost free. Two independent paths, do both:
   - **FotMob's own number**: already captured this session. `laliga/scraper.py`'s
     `_fotmob_shot_xg_list()` reads `expectedGoalsOnTarget` straight off FotMob's
     shotmap and stores it in `_fotmob_shots[].xgot` (see `laliga/scraper.py` near
     `_fotmob_shot_xg_list`, added 2026-09-14). It is captured but **not yet surfaced**
     anywhere — no `xgot_fotmob` field on the matched shot, no UI. This is a data-
     plumbing task, not a modeling task: mirror exactly what `build_match_details.py`
     already does for `xg_fotmob`/`xg_understat` (`_cross_provider_xg_buckets` /
     `_pop_xg`, same file) but for `xgot`.
   - **Our own model**: FotMob's coverage is incomplete (not every match, not every
     shot — same partial-coverage caveat as its plain xG). Train a placement-based
     model on WhoScored's own `GoalMouthY`/`GoalMouthZ` qualifiers (confirmed present
     on every `Goal`/`SavedShot` event — see `laliga_dashboard/xg_model.py`'s
     `shot_xg()` and the raw qualifier dump done 2026-09-14 in this session's
     investigation) so every match gets a consistent number, not just the FotMob-
     covered subset. Training set = shots that reached the goal frame (`Goal` +
     `SavedShot`, i.e. actually on target — `BlockedShot`/`MissedShots` are off by
     definition and excluded), label = 1 if `Goal` else 0, features = placement
     (distance from goal-mouth centre, height, corner/near-post flags) **plus** the
     same pre-shot context features xg_core_v3 already uses (distance/angle/body
     part/big-chance/situation) since placement alone underfits (a keeper's positioning
     interacts with shot type). Mirror `xg_core_v3`'s exact architecture: a new
     `xgot_core/` package with `features.py` (stdlib-only, shares geometry with
     `xg_core/features.py` and `xg_core_v3/features.py` — do not re-derive shot-angle
     math a third time, import it), `score.py` (pure-python runtime), `train.py` (CLI,
     same LR+monotone-GBM blend + cross-fitted isotonic calibration + per-league shift
     pattern as `xg_core/train.py` — see that file's own docstring/CLI for the shape
     to copy), and `xgot_artifact.json` carrying the same `meta`/`league_shifts`/
     `metrics` fields the existing artifacts do. Validate the same way: OOF Brier/AUC,
     reliability table, Σ(on-target-goal-probability) sanity check.

2. **Goalkeeper goals-prevented** — depends on #1 (either xGOT source), otherwise pure
   arithmetic: `Σ xGOT (shots faced) − actual goals conceded`, per keeper, per match/
   season. No new model. Once xGOT exists per shot, add this as a `build_players.py`
   aggregate exactly like `npg`/`fwd_eff` were added this session (see that file's
   `_player_shot_extras`/`aggregate()` for the pattern to copy — accumulate a keeper's
   faced-shots' xGOT the same way striker features accumulate `bc_missed_xg`). Surface
   in the Players table (currently has no goalkeeper-specific columns at all) and
   probably deserves its own Standouts leaderboard card ("Best shot-stopping") — there
   is currently no goalkeeper section anywhere in the dashboard, so this is also the
   natural place to introduce one if the user wants it.

3. **Progressive passes / progressive carries per 90** — no model, direct calculation.
   WhoScored's `Pass` events already carry start `x`/`y` and end coordinates (used
   today by the Pass Explorer / pass network in `match.js` and `xa_features.py`); a
   "progressive" pass/carry is a standard, well-defined threshold (moves the ball a
   fixed % closer to goal, or into the final third/box — StatsBomb's public definition
   is a reasonable default: ≥25% closer to goal if the pass starts in the defensive
   half, ≥10% in the attacking half, always counts if it ends inside the box).
   Dribble/carry events (`is_shootout`-style filtering already exists for dribbles in
   `xg_core/xa_features.py` — check there for the existing dribble-event handling
   before writing new parsing) give the carry side. Add as a `build_players.py`
   aggregate; cheap, fast, ship this early even before xGOT if a quick win is wanted.

4. **PPDA (passes allowed per defensive action)** — no model, direct calculation, team-
   level (not per-player). Needs: opponent's completed passes in your defensive two-
   thirds of the pitch (denominator) ÷ your own tackles+interceptions+fouls in that
   same zone (numerator) — standard definition, lower = more intense pressing. All
   three counted actions already exist in `SUM_STATS`/event parsing
   (`build_players.py`/`build_match_details.py`); this is a team-level aggregate, so it
   likely belongs in `build_data.py` (team-match stats) or a new function alongside
   `team_xg_from_events()` in `xg_model.py`, not `build_players.py`. Surface in Team
   Lab, which already has team-level defensive metrics.

5. **xT (Expected Threat)** — by far the biggest lift, plan this as its own multi-day
   effort, not a quick add. Values *every* pass/carry by how much it raises scoring
   probability, not just shots — the standard "beyond xG" possession-value model
   (Karun Singh's public formulation is the usual reference implementation to study
   first). Needs: (a) a pitch grid (typically 12×8 or 16×12 zones), (b) a Markov-chain
   model over the whole event stream estimating P(shot | zone) and P(move to zone' |
   zone) from transition frequencies, (c) the xT value surface = fixed-point solution
   of shoot-value + move-value over that grid, (d) per-action xT added = value(end
   zone) − value(start zone) for the acting team. This is genuinely new modeling
   research for this codebase — no existing xg_core file gives a template to copy
   (xG/xA are both shot/pass *outcome* models, xT is a *state-value* model, a different
   family entirely). Recommend prototyping this in a notebook/scratch script against
   one season before committing to a `xt_core/` package shape. Do this last, and only
   once xGOT/goals-prevented/progressive-actions/PPDA have proven the "new model
   folder mirrors xg_core_v3's architecture" pattern works smoothly end to end.

## Training corpus (per this session's explicit scope decision: La Liga + EPL only)

Both repos now have genuinely large local raw-match corpora on **this machine**, as of
today (2026-09-14) — confirmed directly, not assumed:

| Repo | Path | Seasons | Matches |
|---|---|---|---|
| XLALIGA | `laliga/matches/<season>/` | 2022-23, 2023-24, 2024-25, 2025-26 (each 380/380, full WhoScored data) + 2026-27 (50+ and growing, pipeline-ready) | ~1900+ |
| XEPL | `epl/matches/<season>/` (sibling repo, `C:\Users\puzik\OneDrive\שולחן העבודה\XEPL`) | 2022-23, 2023-24, 2024-25, 2025-26 (each 380/380) + 2026-27 (39+ and growing) | ~1900+ |

No re-scraping needed to start training any of models #1-4 — the corpus already exists
on disk from this session's two big scraping pushes. World Cup (`XWORLDCUPTWIT/wc2026/
matches`, 131 matches) and Barcelona-specific data (`BCNPROJECT-main`, not yet
inventoried — check its actual data shape before assuming it's WhoScored-compatible
raw match JSON) were explicitly deferred by the user for this round; note that
`xg_core_v3`'s own training run DID include World Cup data in its corpus (per
`xg_core_v3/xg_artifact.json`'s `league_shifts: {"LaLiga", "EPL", "WorldCup"}`), so a
"La Liga + EPL only" xGOT/xT model will be trained on a narrower corpus than the
existing xG model — worth flagging in that model's own README/artifact `meta` so a
future retrain decision (add WC back in) is an informed one, not forgotten.

## Integration touchpoints, once each model exists

Same shape as this session's FotMob-xG-comparison work — don't forget any of these:

- **Builders**: `build_players.py` (player-level aggregates: goals-prevented,
  progressive actions), `build_data.py` or `xg_model.py` (team-level: PPDA, xT team
  totals), `build_match_details.py` (per-shot/per-action fields for the Match Centre).
- **Renderer**: `laliga/renderer.py` mirrors the dashboard's scoring engine for the
  PNG infographics (`xg_core.score`/`xg_core_v3` are both already routed through it) —
  any new *shot-level* model (xGOT) should get the same treatment so PNG and site stay
  consistent, matching the project's own "PNG == site" invariant. Team/possession-level
  models (xT, PPDA) probably don't need a PNG treatment (the infographic is match-
  summary, not a full analytics surface) — a judgment call for whoever builds it.
- **UI**: Players table columns + a Standouts leaderboard card (established pattern
  from Forward Efficiency), a goalkeeper section (doesn't exist yet — first model that
  needs one), Match Centre shot-detail panel (established pattern from the 3-source
  xG comparison), Team Lab (PPDA/xT team-level).
- **Retrain docs**: every new artifact needs the same README treatment
  `xg_core/README.md` gives xG/xA today — trained-date, corpus size, OOF metrics,
  retraining CLI invocation — so a future session (or a fresh instance of the assistant)
  can evaluate and retrain without re-deriving the whole pipeline from scratch, the
  same way this session read that README to understand what already existed.

## Open questions for whoever picks this up

- Goalkeeper section: does it belong in Players (existing table, new columns) or a
  wholly new dashboard tab? No existing precedent in this codebase either way.
- xT's pitch-grid resolution and whether to expose per-player xT-added as a stat, a
  pass-map overlay, or both — needs a design pass once the model itself is validated,
  not before.
- Whether to eventually widen the training corpus back to World Cup (+ Barcelona, once
  its data shape is understood) for xGOT/xT, matching xg_core_v3's broader corpus — the
  "La Liga + EPL only" choice here was explicitly scoped for *this* round, not a
  permanent decision.
