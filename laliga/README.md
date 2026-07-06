# La Liga analytics — pipeline & dashboard

A port of the WC2026 system to Spanish **La Liga**, covering two complete seasons —
**2025/26** and **2024/25** (both fully scraped) — plus **2026/27** (pipeline‑ready, awaiting
fixtures). Same idea: scrape each
match from multiple sources → render a PNG → refresh a static web dashboard → auto‑deploy.
The structural difference is that a league is a **round‑robin table**, not a group+knockout
tournament — so there is a single standings table and a season projection instead of a
bracket.

Live dashboard (once pushed): `laliga_dashboard/index.html` (root `index.html` is a chooser
between La Liga and World Cup 2026).

## Data sources
- **FotMob** — league id **87** ("LaLiga"). Token‑free XML feed
  (`api.fotmob.com/matches?date=`) gives fixtures/results/matchday/team‑ids (the schedule
  spine); `matchDetails` (needs `FOTMOB_XMAS_TOKEN`) adds possession/venue/xG.
- **WhoScored** — the event stream (shots/passes/dribbles/goals/lineups/coords) via the
  `matchCentreData` blob. Selenium, flaky, Cloudflare‑gated → the slow part.
- **Understat** — La Liga xG + **shot‑level xG** + PPDA/deep + player xG/xA
  (`laliga/understat.py`). Replaces SofaScore for La Liga. Now needs Selenium (the site
  bot‑blocks plain HTTP). Overlapping numeric stats are merged; goals/coords stay
  WhoScored‑canonical (see the repo `DATA_SOURCES.md`).

## Two‑layer data model
1. **Schedule spine (token‑free, complete now):** `build_schedule.py` sweeps the FotMob feed
   for league 87 and writes `schedules/SCHEDULE_<season>.json` — every fixture with real
   score + matchday. The dashboard's **standings, results, fixtures and projection** come
   entirely from this. No browser needed.
2. **Rich per‑match layer (browser scrape, fill in over time):** `run_match.py` /
   `backfill.py` deep‑scrape individual games into `matches/<season>/<fotmob_id>.json`,
   which adds xG maps, shot/pass/dribble maps, the All‑Goals‑Map and player stats. The
   dashboard degrades gracefully — a match shows its score/table contribution immediately
   and its rich views once it's been deep‑scraped.

## Commands
```bash
# 1) Build / refresh the schedule (real results) — token-free, ~2 min
py laliga/build_schedule.py --season 2025-26
py laliga/build_schedule.py --season 2026-27      # once FotMob lists the 26/27 fixtures

# 2) Club crests (one-off; self-contained site) + dashboard data
py laliga/download_crests.py
py laliga_dashboard/build_data.py

# 3) Deep-scrape ONE match (needs Chrome + FOTMOB_XMAS_TOKEN)
py -m laliga.run_match --fotmob-id <id> --season 2025-26 --no-push --no-post

# 4) Batch backfill a season (overnight; one push at the end)
py laliga/backfill.py --season 2025-26 --push
py laliga/backfill.py --season 2025-26 --limit 10   # small test run first

# 5) Preview the dashboard locally (serves repo root)
py -m http.server 8778   # then open http://localhost:8778/laliga_dashboard/index.html
```

## Making 2026/27 go live
1. When FotMob publishes the fixtures: `py laliga/build_schedule.py --season 2026-27`
   (fills `SCHEDULE_2026-27.json`), then `py laliga_dashboard/build_data.py`.
2. Arm per‑match Task Scheduler jobs at kickoff+3h: run `laliga/register_tasks.ps1`
   (reads the season schedule). Each fires `py -m laliga.run_match --fotmob-id <id>
   --season 2026-27`, which scrapes → renders → refreshes → pushes.

## Config (`.env`, reused from the WC pipeline)
- `FOTMOB_XMAS_TOKEN` — FotMob matchDetails token (xG/possession/venue). Optional; without
  it the schedule + Understat still give results + xG.
- `GIT_TOKEN` — required for the auto‑push (`git_ops.py`).
- Optional overrides: `LALIGA_FOTMOB_LEAGUE_ID` (default 87), `LALIGA_WHOSCORED_URLS`
  (season‑specific WhoScored fixtures page), `UNDERSTAT_FALLBACK=0` to skip Understat.

## Files
`build_schedule.py` schedule spine · `scraper.py` 3‑source scrape+merge · `understat.py`
Understat source · `run_match.py` one‑shot orchestrator · `backfill.py` batch · `renderer.py`
PNG · `git_ops.py` deploy · `team_colors.py` 20 clubs · `download_crests.py` badges ·
`register_tasks.ps1`/`unregister_tasks.ps1` scheduler. Dashboard builders live in
`../laliga_dashboard/`. Published PNGs go in `../laliga_png/` (not `LaLiga/` — the filesystem
is case-insensitive, so that would alias the `laliga/` pipeline folder).
