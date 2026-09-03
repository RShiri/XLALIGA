# Prompt: update the shared xG model (xg_core_v3) with the new 2026-27 season

Paste this into a **fresh Claude Code session** (Sonnet or Opus) opened at
`C:\Users\puzik\XG V3` — that's the standalone training repo, separate from the four
dashboard repos (XLALIGA / XWORLDCUPTWIT / XEPL / BCNPROJECT-main) that just *consume* the
trained model. Read `README.md` and `ROADMAP.md` there first; they document this exact
workflow (the "Adding future seasons" recipe) in more depth than this prompt repeats.

## Context

- The live xG model (`xg_core_v3`, 23-feature LR + GBM + market blend) was trained
  2026-07-07 on **77,627 shots**: La Liga 2022-23 → 2025-26 (4 seasons), EPL 2022-23 →
  2025-26 (4 seasons), and the 2026 World Cup. Out-of-fold: Brier 0.0723, AUC 0.805,
  ΣxG ≈ goals per league.
- **La Liga 2025-26 season has now started** (2026-27, currently ~30 matches played). Its
  raw scraped match files live in `XLALIGA\laliga\matches\2026-27\*.json` (WhoScored deep
  scrapes via `laliga/scrape_whoscored.py` — same format every other season's raw files
  use) but are **not yet in `C:\Users\puzik\XG V3\data\raw\laliga\`**, so none of this
  season's shots are in the training corpus yet.
- This is a small, incremental addition (~30 matches against a 77k-shot corpus), not an
  urgent fix — a live comparison run on 2026-27's matches so far (2026-09-03) found the
  *current* model already tracks actual goals more closely (ratio 0.98) than either
  FotMob's (1.11) or Understat's (1.18) own published xG on the same matches. So: this is
  routine database growth, not a quality problem to chase. Use your judgment on whether
  enough of the season has accumulated to be worth a retrain yet, or whether to wait for
  more matchdays first — there's no deadline.

## What to actually do

Follow `ROADMAP.md`'s "Adding future seasons" recipe, from `C:\Users\puzik\XG V3`:

1. **Copy the new raw matches in:**
   ```powershell
   robocopy "C:\Users\puzik\OneDrive\שולחן העבודה\XLALIGA\laliga\matches\2026-27" `
     "C:\Users\puzik\XG V3\data\raw\laliga\2026-27" *.json /MT:16 /NFL /NDL /NJH /NJS
   ```
   (Check XLALIGA's `laliga/matches/2026-27/` folder for the current match count first —
   more may have been scraped since 2026-09-03. Also check whether XLALIGA has scraped any
   *more* than XWORLDCUPTWIT/XEPL's copies, or vice versa, and pull from whichever has more
   — they're supposed to be the same data but can drift.)
2. **Rebuild the database:** `py rebuild_database.py` (auto-discovers every season under
   `data\raw\`).
3. **Retrain:**
   ```powershell
   py train_v3_features.py
   Copy-Item artifacts\xg_v3_features.json xg_core_v3\xg_artifact.json -Force
   ```
4. **Validate:** `py validate_v3_scorer.py` — ship-worthy only when OOF Brier / log-loss /
   AUC don't regress and ΣxG ≈ goals stays true per league. Report the before/after numbers.
5. **Redeploy the updated artifact** to every dashboard repo that carries its own copy of
   `xg_core_v3/xg_artifact.json` (each is a self-contained runtime folder, no shared
   package/symlink — this has to be a literal file copy):
   - `C:\Users\puzik\OneDrive\שולחן העבודה\XLALIGA\xg_core_v3\xg_artifact.json`
   - `C:\Users\puzik\OneDrive\שולחן העבודה\XWORLDCUPTWIT\xg_core_v3\xg_artifact.json`
   - `C:\Users\puzik\OneDrive\שולחן העבודה\XEPL\xg_core_v3\xg_artifact.json` (if present —
     confirm first; XEPL may still be on the older `xg_core`)
   - `C:\Users\puzik\OneDrive\שולחן העבודה\BCNPROJECT-main\xg_core_v3\xg_artifact.json` (if
     present)
   Do **not** touch anything else in those repos, and don't rebuild/push their dashboard
   data from this session — that's a separate step the user does per-repo (each has its own
   `build_data.py` / `build_split.py` rebuild sequence documented in its own CLAUDE.md).

## Guardrails

- This is a **local-only** training repo (`C:\Users\puzik\XG V3` is deliberately outside
  OneDrive/git — see the README's Backup warning). Don't try to `git init`/commit/push
  anything there.
- Don't touch the live dashboard repos beyond copying the one retrained artifact file into
  each — no data rebuilds, no commits, no pushes. Report what changed and let the user
  decide when to rebuild/ship each site.
- If the retrain makes calibration *worse* on any league, say so plainly and don't deploy
  it — keep the current artifact live and report why (e.g. too few new shots yet, a data
  quality issue in the new season's scrapes).
