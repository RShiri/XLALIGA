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

## What we actually capture, field by field

Audited directly against live scraped data on 2026-09-14 (not from memory) — "captured"
means it reaches `laliga/matches/<season>/<id>.json` or a `build_*` output; "fetched,
unused" means the API response has it but nothing parses it into our schema yet (see
`DATA_AUDIT_AND_STATS_PLAN.md` for the full plan on closing these gaps).

### Match-level

| Field | Source | Status |
|---|---|---|
| Score, matchday, date, venue name/city, stage | FotMob (spine) / WhoScored (real fulltime score wins on conflict) | ✅ captured |
| Attendance | FotMob `infoBox.Attendance` | ⚠️ fetched, unused (schema field exists, usually left `null`) |
| Exact stadium (capacity, lat/long, surface) | FotMob `infoBox.Stadium` | ⚠️ fetched, unused |
| Referee (name, nationality, career card/foul/penalty rates) | FotMob `matchFacts.infoBox.Referee` | ⚠️ fetched, unused |
| Weather (temp, wind, humidity, precipitation, conditions) | FotMob `content.weather` | ⚠️ fetched, unused |
| Head-to-head history | FotMob `content.h2h` | ⚠️ fetched, unused |
| Player of the match, top performers | FotMob `matchFacts.playerOfTheMatch`/`topPlayers` | ⚠️ fetched, unused |

### Team-level (per match)

| Stat | Source(s) | Status |
|---|---|---|
| Possession, passes, pass accuracy, duels won | FotMob + WhoScored (merged, "keep the larger/most-balanced" rule) | ✅ captured |
| Shots, shots on target, big chances created/missed, corners, offsides, fouls, saves, blocks, clearances, interceptions | FotMob + WhoScored | ✅ captured |
| Team xG | FotMob (official) **and** our own calibrated model (xg_core_v3, from WhoScored shot events) **and** Understat | ✅ captured — all three shown side by side, never blended (Data tab + Match Centre) |
| PPDA, deep completions | Understat (league-row level only) | ⚠️ partially available; not wired into any builder or the dashboard yet |
| Market value (squad total), age/nationality mix | FotMob lineup player objects, summable | ⚠️ fetched per-player, not aggregated to team level |

### Player-level (per match / aggregated season)

| Stat | Source | Status |
|---|---|---|
| Goals, assists, non-penalty goals, penalties, cards, minutes, rating | WhoScored events + stats | ✅ captured |
| xG, xA (calibrated models), xG involvement, finishing over/under xG | Our own models (xg_core_v3 + xg_core's xA), from WhoScored shots/passes | ✅ captured |
| Non-penalty xG, big chances missed (count + xG value), Forward Efficiency | Derived from WhoScored shot qualifiers (`BigChance`, `Penalty`) | ✅ captured (added 2026-09-14) |
| Shots, shots on target, key passes, passes, pass %, touches, tackles, interceptions, aerials won, dribbles, fouls, clearances, dispossessed, saves | WhoScored per-minute stat streams | ✅ captured |
| Per-shot xG from FotMob and Understat, matched to the real shot | FotMob shotmap + Understat shots, best-effort matched by team+surname+minute | ✅ captured (added 2026-09-14, Match Centre shot detail panel) |
| Per-shot xGOT (expected goals *on target*) | FotMob shotmap `expectedGoalsOnTarget` | ⚠️ fetched and stored (`_fotmob_shots[].xgot`), not yet matched to real shots or surfaced — see `xg_core/PLAN_new_models.md` item 1 |
| Age, nationality, market value | FotMob lineup player objects | ⚠️ fetched, unused — no field in `build_players.py`'s player record at all |
| Profile photo | FotMob (`images.fotmob.com/image_resources/playerimages/<id>.png`, confirmed live) | ⚠️ URL pattern known and free, but needs FotMob-id↔WhoScored-id matching first — no shared id exists; see `DATA_AUDIT_AND_STATS_PLAN.md` |
| Progressive passes/carries, PPDA (team pressing) | Derivable from existing pass/tackle coordinates — no new scrape needed | ❌ not built — see `xg_core/PLAN_new_models.md` items 3-4 |
| xGChain / xGBuildup (possession-chain xG credit) | Understat per-shot fields | ⚠️ fetched (where Understat coverage exists), unused |
| Goalkeeper-specific: claims, punches, sweeper actions, penalty saves | WhoScored `KeeperSweeper`/`Claim`/`Punch`/`Smother`/`KeeperPickup`/`PenaltyFaced` events | ❌ not built — no goalkeeper section exists anywhere in the dashboard yet |

### Event-level (raw WhoScored stream — 38 distinct event types scraped)

All of these reach `events` in the raw match JSON; only the ones marked ✅ are
aggregated into any stat today (counted directly in a 40-match 2025-26 sample):

| Event type | Sample count | Used for |
|---|---|---|
| Pass | 38,365 | ✅ pass network, xA, key passes |
| Shot types (`Goal`/`SavedShot`/`MissedShots`/`ShotOnPost`/`BlockedShot`) | ~980 combined | ✅ xG/xGOT, shot maps, goals timeline |
| BallRecovery, BallTouch, Aerial, Clearance, TakeOn, Tackle, Interception, BlockedPass, Challenge, Dispossessed | 2,000–3,200 each | ✅ box-score counts only; ❌ no zone/context breakdown |
| Foul, Card | ~1,976 / 169 | ✅ counts; ❌ no referee-correlated or location analysis |
| CornerAwarded | 772 | ❌ unused — corner-taker/delivery-type stats not built |
| SubstitutionOn/Off | 374 each | ✅ minutes played only |
| OffsideGiven/OffsidePass/OffsideProvoked | 130 each | ❌ unused |
| FormationChange, FormationSet | 128 / 80 | ❌ unused — no formation/tactics timeline anywhere |
| Save, KeeperPickup, KeeperSweeper, Claim, Punch, Smother, PenaltyFaced | ~1,145 combined | ❌ unused — see goalkeeper stats above |
| Error | 54 | ❌ unused — "error leading to shot/goal" stat not built |
| GoodSkill | 7 | ❌ unused — flair/skill-move stat not built |

### Genuinely not available from any current source
See `DATA_AUDIT_AND_STATS_PLAN.md` Part 2 for the full breakdown — in short: player
tracking/positional data (needs a paid provider, no free source exists), transfer
history and injury timelines (Transfermarkt, not yet scraped), betting odds (not
scraped), VAR decision detail and crowd sentiment (not reliably available from any
source, not recommended to pursue).
