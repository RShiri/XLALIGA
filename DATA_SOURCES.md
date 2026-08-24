# Data sourcing

Every match is drawn from **three** sources; where they report the same metric the published
value is (moving toward) the average of the sources that returned it. One source is a fallback,
not the goal.

## The three sources
- **FotMob** — league id **87** ("LaLiga"; NOT "LaLiga2" 901075). The schedule spine comes from
  the site API under `/api/data/` (since Aug 2026): `www.fotmob.com/api/data/leagues?id=87&
  season=2026%2F2027` returns the whole season — fixtures, results, round numbers — in **one**
  request, with `www.fotmob.com/api/data/matches?date=YYYYMMDD` as the per-day fallback.
  The old token-free XML feed `api.fotmob.com/matches?date=` is now **live-only**: it answers
  with root `<live>/<exmatches>` listing just the games in play and ignores `?date`, so it can
  no longer build a schedule. `matchDetails` (optionally `FOTMOB_XMAS_TOKEN`) still adds
  possession/venue/xG.
- **WhoScored** — the **event stream** (shots, passes, dribbles, goals, saves, lineups,
  coordinates) via the `matchCentreData` blob. Selenium; the richest spatial data; drives the
  shot/pass/dribble maps and the All-Goals-Map.
- **Understat** — La Liga **xG + shot-level xG** + PPDA/deep + player xG/xA (`laliga/understat.py`).
  Now needs Selenium (the site bot-blocks plain HTTP). This replaces SofaScore, which the WC
  system used — Understat is the natural, free La Liga xG source.

## What must NOT be averaged
- **Score / goals** — single-source (WhoScored event stream when present, else the FotMob
  result); never averaged.
- **Event coordinates / shot & pass geometry** — kept **WhoScored-canonical** (the renderer /
  `xg_model.py` orientation is tuned to it). Understat shots are a secondary xG check, not
  mixed into the maps.
- **Lineups** — reconciled, not numerically averaged.

## Keep PNG and website in sync
`laliga/renderer.py` (PNGs) and `laliga_dashboard/xg_model.py` + the `build_*` builders must use
the same merged numbers, or the infographics and the live site will disagree.
