# Prompt: build the new models from the future-models plan (xGOT, goals-prevented, progressive actions, PPDA, xT)

Paste this into a **fresh Claude Code session**. Where it starts depends on which item
you're doing — see each item below; this is **not** a single-repo task like the other
two prompts in this folder.

## Context — read this before picking an item

`xg_core/PLAN_new_models.md` (XLALIGA repo root) is the detailed plan this prompt
executes — read it in full first, it has the architecture, feature lists, and
validation approach for each model. **One correction to that plan, discovered after it
was written**: it assumes a new model gets trained inside XLALIGA's `xg_core/` folder.
That's how the *old* `xg_core` (v2) and its xA model work, but the **live shot-xG
model (`xg_core_v3`) is actually trained in a separate standalone repo**,
`C:\Users\puzik\XG V3` (outside OneDrive/git on purpose — see its own README). That
repo already has the full La Liga + EPL (+ World Cup) raw corpus rebuilt and a proven
retrain/validate recipe (`ROADMAP.md`'s "Adding future seasons" section, and
`UPDATE_XG_CORE_PROMPT.md` in this same folder for a worked example of using it).
**Any new *shot-outcome* model (xGOT is one) should very likely be trained there too**,
not in XLALIGA's `xg_core/` — reusing `rebuild_database.py`'s corpus and the existing
validate/deploy pattern rather than standing up a parallel one. Confirm this makes
sense once you're actually in that repo (read its README/ROADMAP yourself) before
committing to it, but treat it as the default assumption, not `xg_core/PLAN_new_models.md`'s
original one.

Items #2-4 below (goals-prevented, progressive actions, PPDA) are **not** models in
the training sense — pure arithmetic over existing event data — and belong in
XLALIGA/XEPL's own `build_players.py`/`build_data.py`, same as Forward Efficiency was
added this session. Only #1 (xGOT) and #5 (xT) are real training work that might
belong in `XG V3`.

## Item 1 — xGOT (do this first; the other items build on it or are independent, but this is the biggest question mark to resolve early)

Two parts, both described in `xg_core/PLAN_new_models.md`'s item 1:
- FotMob's own per-shot `expectedGoalsOnTarget` is **already captured** as of
  2026-09-14 (`laliga/scraper.py`'s `_fotmob_shot_xg_list()` stores it in
  `_fotmob_shots[].xgot`) but not yet matched to real shots or surfaced anywhere — this
  half is a `build_match_details.py` plumbing task (mirror the existing
  `xg_fotmob`/`xg_understat` matching exactly, just add `xgot_fotmob`), doable
  entirely from XLALIGA, no new training needed. Do this part regardless of what you
  decide about the second part below.
- Training our **own** placement-based xGOT model (so every match gets a number, not
  just FotMob-covered ones) is the real modeling work — start in `C:\Users\puzik\XG V3`
  per the correction above, reusing its corpus, UNLESS you determine after reading that
  repo's own docs that it's not the right fit (e.g. if it's scoped narrowly enough to
  "shot happened or not" that bolting on a placement-conditional model is awkward) —
  in that case fall back to `xg_core/PLAN_new_models.md`'s original plan (a new
  `xgot_core/` folder inside XLALIGA, mirroring `xg_core_v3`'s architecture locally).
  Make this call explicitly and say why, don't silently pick one.

## Item 2 — Goalkeeper goals-prevented

Depends on item 1 existing (either FotMob's or a trained model). Pure arithmetic once
xGOT exists per shot: `Σ xGOT faced − actual goals conceded`, per keeper. Lives in
XLALIGA's `build_players.py` (and mirror in XEPL's, same file shape) — see
`xg_core/PLAN_new_models.md` item 2 for the exact aggregation pattern to copy (matches
how `bc_missed_xg` was added to `_player_shot_extras`/`aggregate()` this session).
**No dashboard has a goalkeeper section today** — decide where this surfaces (extend
the Players table with GK-only columns, or a new tab) as part of this work, it's an
open design question in the plan doc, not a solved one.

## Item 3 — Progressive passes/carries per 90

No model, direct calculation from existing pass/dribble end-coordinates. See
`xg_core/PLAN_new_models.md` item 3 for the threshold definition to use (StatsBomb's
public one is the suggested default). Independent of items 1-2 — fine to build this
first if it's the easiest win you want to bank early. XLALIGA `build_players.py` +
mirror in XEPL.

## Item 4 — PPDA (team pressing intensity)

No model, team-level (not per-player) arithmetic from existing tackle/interception/
pass counts. See `xg_core/PLAN_new_models.md` item 4 — likely belongs in
`build_data.py` or `xg_model.py` (team-level), not `build_players.py`. Independent of
everything else here.

## Item 5 — xT (Expected Threat) — do this last, treat as its own project

By far the biggest lift (grid-based possession-value model, a genuinely different
model family from xG/xA/xGOT — see `xg_core/PLAN_new_models.md` item 5 for why no
existing file is a template to copy from). Don't start this until items 1-4 have
shipped and you have a feel for how the "new model folder, mirror xg_core_v3's shape"
pattern goes in practice. Prototype against one season before committing to a package
layout. This one might also belong in `C:\Users\puzik\XG V3` (same corpus-reuse logic
as xGOT) or might not (it's not a shot-outcome model, needs the full event stream
including all passes/carries, not just shots) — genuinely undecided, make the call
once you're actually doing the work and can see what the data plumbing wants.

## Guardrails (apply to all items)

- Every new artifact needs the same README treatment `xg_core/README.md` gives xG/xA
  today (trained-date, corpus size, OOF metrics, retrain CLI) — don't ship a model
  without one.
- Training corpus scope for new models: this plan was explicitly scoped to **La Liga +
  EPL only** when it was written (2026-09-14) — narrower than `xg_core_v3`'s own
  corpus, which also includes the World Cup. That was a deliberate choice for that
  session, not a permanent constraint; revisit whether to include World Cup (+
  Barcelona/BCNPROJECT data, not yet inventoried) for real, especially if you're
  training in `XG V3` where that corpus already sits ready to use.
- Match `xg_core_v3`'s validation bar for anything shipped as a shot-outcome model:
  OOF Brier/log-loss/AUC don't regress, reliability table has no systematic bias, ΣxG
  (or ΣxGOT, etc.) ≈ actual outcomes per league.
- Same push discipline as the rest of this session: commit as you go in whichever
  dashboard repo(s) you touch, but confirm with the user before pushing to `main` —
  both XLALIGA's and XEPL's `main` currently reflect a fully working, verified state.
- If working in `C:\Users\puzik\XG V3`: it's local-only (deliberately outside git/
  OneDrive), never try to `git init`/commit/push anything there, and never rebuild or
  push a dashboard repo's live data from that session — report what changed and let a
  separate session (or the user) handle deploying a retrained artifact, exactly like
  `UPDATE_XG_CORE_PROMPT.md` already establishes as the pattern.
