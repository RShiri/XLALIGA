# XLALIGA — La Liga match analytics

Multi-source analytics for Spanish **La Liga**: a live standings + results dashboard, an xG
efficiency lab, a per-match "Match Centre" (shot/pass/dribble maps, all-goals reconstruction),
and a Poisson **season projection** (title / European / relegation odds). Covers season
**2025/26** (live) and **2026/27** (pipeline-ready). Ported from the WorldCup2026 analytics
system to a round-robin league.

**Live dashboard:** `laliga_dashboard/index.html` (root `index.html` redirects there).
On GitHub Pages: `https://rshiri.github.io/XLALIGA/`.

## How it works
Two data layers:
1. **Schedule spine (token-free):** `laliga/build_schedule.py` sweeps FotMob's public feed
   for La Liga (league 87) → `laliga/schedules/SCHEDULE_<season>.json` with every fixture's
   real score + matchday. This drives the **standings, results, fixtures and projection** —
   no browser needed.
2. **Rich per-match layer:** `laliga/run_match.py` / `laliga/backfill.py` deep-scrape
   individual games (FotMob + WhoScored + Understat) into `laliga/matches/<season>/<id>.json`,
   adding xG, shot/pass/dribble maps and player stats. The dashboard degrades gracefully —
   a match shows its result/table contribution immediately, its rich views once deep-scraped.

## Quick start
```bash
pip install -r requirements.txt
py laliga/build_schedule.py --season 2025-26     # real results (token-free)
py laliga/download_crests.py                      # club badges
py laliga_dashboard/build_data.py                 # build the dashboard data
py -m http.server 8778                            # open http://localhost:8778/laliga_dashboard/index.html
```
Deep-scrape (needs Chrome + `FOTMOB_XMAS_TOKEN`; see `.env.template`):
```bash
py laliga/backfill.py --season 2025-26 --limit 5   # test run
py laliga/backfill.py --season 2025-26 --push      # full season + deploy
```

## Data sources
**FotMob** (league 87 — fixtures/results/xG), **WhoScored** (event stream: shots/passes/
dribbles/coords), **Understat** (xG + shot-level xG + PPDA + player xG/xA). See
[`DATA_SOURCES.md`](DATA_SOURCES.md). Full operating guide: [`laliga/README.md`](laliga/README.md).

## Layout
`laliga/` pipeline · `laliga_dashboard/` static site + builders · `laliga_png/` published PNGs ·
`team_logos/laliga/` crests.
