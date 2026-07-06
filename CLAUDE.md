# CLAUDE.md — XLALIGA project guide (read this first)

**La Liga match analytics.** Two outputs from one scraped dataset: an interactive **web
dashboard** (`laliga_dashboard/`, static site) and a per-match **PNG infographic**
(`laliga/renderer.py`). Ported from a World Cup 2026 analytics system to a round-robin league.

- **Live site:** https://rshiri.github.io/XLALIGA/  (root `index.html` redirects to `laliga_dashboard/`)
- **GitHub:** https://github.com/RShiri/XLALIGA  (public; GitHub Pages serves `main` root)
- **This folder** is a git clone linked to that repo (`origin` → XLALIGA.git). Commit + push here.

## CURRENT STATE (as of 2026-07)
- **Two full seasons live.** Both are 380/380 played, **380 with xG**, full standings + season
  projection, and a per-match Match Centre — **761** interactive match-centre pages across the
  two. Pick either from the dashboard's season switcher:
  - **2025/26** — complete, **600 players**. The default season.
  - **2024/25** — complete, **589 players** (added via an archived-season WhoScored scrape).
- **2026/27 = pipeline-ready, empty.** `laliga/schedules/SCHEDULE_2026-27.json` is a placeholder.
  When FotMob publishes the fixtures, one command fills it (see below) and the dashboard's
  season switcher shows it.
- **Caveat — the 2025/26 schedule spine in this clone is stale.** The shipped
  `data.js`/`players.js`/`shots.js` (rebuilt 2026-07-04) carry the full 380-match 2025/26, but
  `schedules/SCHEDULE_2025-26.json` here is an older 151-match spine. Re-run
  `build_schedule.py --season 2025-26` before rebuilding derived data, or 2025/26 will regress
  to 151 played.

## Repo layout
```
index.html                     chooser/redirect → laliga_dashboard/
laliga_dashboard/              the website (static, no build step at view time)
  index.html match.html        main dashboard + per-match "Match Centre"
  app.js match.js              front-end (app.js = league views; match.js = match centre)
  styles.css match.css
  data.js                      window.LL_DATA, season-keyed  ← generated
  players.js                   window.LL_PLAYERS, season-keyed ← generated
  shots.js                     window.LL_SHOTS, season-keyed (Team Lab shot maps) ← generated
  matches_detail/<id>.js       per-match shots/passes/dribbles/goals/lineups ← generated (SHIPPED)
  database/                    CSV + sqlite exports ← generated
  build_data.py build_players.py build_match_details.py build_database.py build_shots.py  builders
  xg_model.py                  shared shot-extraction + xG/xA (routes through xg_core/)
xg_core/                       THE CANONICAL calibrated models: v2 xG + pass-level xA
                               artifacts + XGScorer/XAScorer + training CLIs (see its
                               README; XWORLDCUPTWIT + BCNPROJECT carry vendored copies)
laliga/                        the pipeline
  build_schedule.py            FotMob token-free sweep → schedules/SCHEDULE_<season>.json
  scrape_whoscored.py          bulk WhoScored crawler (the main backfill tool — see below)
  run_match.py                 one-shot per match: scrape→render→refresh→push→whatsapp
  backfill.py                  batch wrapper over run_match
  scraper.py understat.py      3-source scrape/merge (FotMob 87 + WhoScored + Understat)
  renderer.py                  matplotlib PNG
  git_ops.py                   auto-deploy (clone+commit+push generated files + PNG)
  team_colors.py               20 clubs
  download_crests.py           club badges → team_logos/laliga/
  register_tasks.ps1           Windows Task Scheduler (per-match live auto-runs, for 26/27)
  schedules/                   SCHEDULE_<season>.json (fixtures + results + matchday)
  matches/<season>/<id>.json   raw scrapes — GIT-IGNORED (huge; see gotchas)
laliga_png/                    published PNGs (tracked)
team_logos/laliga/             20 crests
```

## Data model — two layers
1. **Schedule spine (token-free, no browser).** `build_schedule.py` sweeps FotMob's public
   feed (`api.fotmob.com/matches?date=`) for **league 87** ("LaLiga") → every fixture with
   real score + matchday. Drives **standings / results / fixtures / projection**.
2. **Rich per-match layer (browser scrape).** Each played match is deep-scraped from WhoScored
   into `laliga/matches/<season>/<id>.json`; the builders derive `matches_detail/<id>.js`
   (the shot/pass/dribble maps + all-goals-map + lineups) and player/xG aggregates. The site
   degrades gracefully — a match shows its result immediately and its rich views once scraped.

`window.LL_DATA` is **keyed by season**; the dashboard has a season switcher. League, not
tournament: a single standings table (UCL/UEL/Conference/relegation zones) + a Poisson season
projection replace the WC group tables / knockout bracket.

## How to run / update
```bash
# view locally (from this folder)
py -m http.server 8778     # → http://localhost:8778/laliga_dashboard/index.html

# refresh 2025/26 results/standings (fast, token-free)
py laliga/build_schedule.py --season 2025-26
py laliga_dashboard/build_data.py

# bring 2026/27 online once FotMob lists fixtures
py laliga/build_schedule.py --season 2026-27
py laliga_dashboard/build_data.py
powershell -File laliga/register_tasks.ps1 -Season 2026-27   # arm per-match live auto-runs

# (re)scrape rich per-match data (needs Chrome; ~1h for a full season)
py laliga/scrape_whoscored.py --season 2025-26                # full season (resumable)
py laliga/scrape_whoscored.py --season 2025-26 --ids 1914240  # specific WhoScored id(s)
# then rebuild everything (build_shots.py reads matches_detail → shots.js for the Team Lab):
py laliga_dashboard/build_match_details.py && py laliga_dashboard/build_players.py \
  && py laliga_dashboard/build_database.py && py laliga_dashboard/build_shots.py \
  && py laliga_dashboard/build_data.py
```
**`scrape_whoscored.py` is the workhorse** for rich data: WhoScored match ids aren't
range-enumerable, so it pages the **weekly** fixtures calendar back (`#dayChangeBtn-prev`),
scrapes each `/Matches/<id>/Live` `matchCentreData`, and maps it to the schedule by team names.
Resumable (skips matches already saved with events).

## Deploy / push
- `.env` is git-ignored — copy `.env.template` → `.env` and set `GIT_TOKEN` (GitHub PAT with
  `repo` scope) + optionally `FOTMOB_XMAS_TOKEN`. **`XWORLDCUPTWIT_REPO` must point at this repo**
  (`https://github.com/RShiri/XLALIGA.git`) so the auto-deploy pushes here, not the WC repo.
- Manual push after rebuilding: `git add -A && git commit -m "…" && git push` (first push asks
  for GitHub auth). GitHub Pages redeploys in ~1 min; hard-refresh (Ctrl+F5).
- `run_match.py` auto-pushes generated files via `git_ops.py` when `GIT_TOKEN` is set.

## Gotchas (hard-won — don't re-break these)
- **Rebuilding derived data in THIS clone needs `LALIGA_MATCH_DIR`** — the raw scrapes are
  git-ignored and absent here; point it at the dev copy before running the builders:
  `$env:LALIGA_MATCH_DIR = "..\XWORLDCUPTWIT\laliga\matches"`. The old
  `tools/regen_unified.py` path can't feed the pass-level xA model (derived files lack
  full pass qualifiers) — use the canonical builders.
- **xG/xA come from `xg_core/` artifacts** (no hard-coded coefficients anywhere anymore).
  Retrain with `py -m xg_core.train` / `py -m xg_core.train_xa`, then copy `xg_core/` to
  XWORLDCUPTWIT and BCNPROJECT-main so all three stay on identical models.
- **undetected-chromedriver is broken on Chrome 149** (SessionNotCreatedException). The scraper
  falls back to **plain Selenium** (Selenium Manager) which works. The fallback catches
  `except Exception` (not just `ImportError`) — keep it that way.
- **Team-matcher must NOT strip "real".** `scrape_whoscored._key` used to collapse
  "Real Madrid"→"madrid", which substring-matched "atletico**madrid**" and **scrambled the two
  Madrid clubs' fixtures**. Keep "real"; verify mapping collision-free before a bulk re-scrape.
- **players.js fields are `g`/`a`/`xg`/`mp`** (not `goals`/`assists`). `app.js` reads those.
- **Publish dir is `laliga_png/` NOT `LaLiga/`** — the filesystem is case-insensitive, so
  "LaLiga" aliases the `laliga/` code folder. Env var `LALIGA_PNG_SUBDIR`.
- **Raw match JSONs are gitignored** (`laliga/matches/20*/*.json`, ~2 MB each, 769 MB/season).
  The dashboard ships the derived `matches_detail/*.js` (~74 MB) instead. If you re-scrape,
  don't commit the raw folder.
- **Understat** changed its site (data now loads via AJAX, not `JSON.parse` blobs), so the bulk
  source is **WhoScored** (its events give shot/pass/dribble maps, players, and estimated xG via
  `xg_model.py`). `laliga/understat.py` is kept for match-level xG when a `FOTMOB_XMAS_TOKEN`
  isn't available, but needs updating for the new Understat structure.
- **No FotMob matchDetails token** by default → official xG/possession/venue aren't fetched;
  xG shown is estimated from WhoScored shots (same model as the PNGs). Set `FOTMOB_XMAS_TOKEN`
  in `.env` to add official figures.
- **Season "finished" state:** with all 380 played, the Projection tab shows the final table
  (no remaining fixtures to simulate) — that's expected.

## Ideas / next steps (optional)
- Backfill the PNG infographics (`renderer.py`) for each match into `laliga_png/`.
- Update `laliga/understat.py` to the new Understat AJAX structure for a second xG source.
- Add earlier seasons (23/24, …) — `build_schedule.py --season 2023-24` + a WhoScored scrape
  (24/25 is already done; follow the same archived-season path).
- Wire the `database/` CSV/sqlite downloads into the Data tab UI.

## Two local copies (avoid confusion)
- **This folder** (`Desktop\XLALIGA`) — the clean repo linked to GitHub. **Use this going forward.**
- `Desktop\XWORLDCUPTWIT\laliga*` — the original dev copy (has the 769 MB raw matches locally).
  Same code; the WC2026 system also lives there. Not linked to XLALIGA.
