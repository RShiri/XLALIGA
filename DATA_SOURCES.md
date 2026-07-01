# Data sourcing

Every match is drawn from **three** sources; where they report the same metric the published
value is (moving toward) the average of the sources that returned it. One source is a fallback,
not the goal.

## The three sources
- **FotMob** — league id **87** ("LaLiga"; NOT "LaLiga2" 901075). The token-free XML feed
  (`api.fotmob.com/matches?date=`) gives fixtures/results/matchday/team-ids (the schedule
  spine); `matchDetails` (needs `FOTMOB_XMAS_TOKEN`) adds possession/venue/xG.
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
