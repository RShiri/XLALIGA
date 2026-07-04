# XLALIGA — La Liga match analytics

Multi-source analytics for Spanish **La Liga**: a standings + results dashboard, an xG
efficiency lab, a per-match "Match Centre" (shot/pass/dribble maps, all-goals reconstruction),
a player leaderboard, and a Poisson **season projection** (title / European / relegation odds).
Ported from the WorldCup2026 analytics system to a round-robin league.

**Status:** **2025/26 is complete** — all **380** matches scraped (380 with xG, 600 players,
380 interactive match-centre pages). **2026/27** is pipeline-ready (empty until FotMob lists the
fixtures). New here? Read [`CLAUDE.md`](CLAUDE.md) — the full project guide, current state,
commands, and gotchas.

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
Rich per-match data — the workhorse is the WhoScored crawler (needs Chrome; ~1h/season, resumable):
```bash
py laliga/scrape_whoscored.py --season 2025-26     # scrape every match's events
py laliga_dashboard/build_match_details.py && py laliga_dashboard/build_players.py \
  && py laliga_dashboard/build_database.py && py laliga_dashboard/build_data.py
git add -A && git commit -m "refresh data" && git push
```

## Data sources
**FotMob** (league 87 — fixtures/results/xG), **WhoScored** (event stream: shots/passes/
dribbles/coords), **Understat** (xG + shot-level xG + PPDA + player xG/xA). See
[`DATA_SOURCES.md`](DATA_SOURCES.md). Full operating guide: [`laliga/README.md`](laliga/README.md).

## Layout
`laliga/` pipeline · `laliga_dashboard/` static site + builders · `laliga_png/` published PNGs ·
`team_logos/laliga/` crests.
