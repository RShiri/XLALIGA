# Data audit + stats/models plan

**Status: planning only, written 2026-09-14 for a future session.** Everything below is
grounded in a direct inspection of the raw scraped data this session (not assumed) —
see the specific evidence under each item. Companion to `xg_core/PLAN_new_models.md`
(that doc covers xGOT/xT/goals-prevented in depth; this one is the wider "what else is
already sitting in our data, unused" audit, plus a real answer on what genuinely isn't).

## Part 1 — Available data we already scrape but don't use

The single biggest finding of this audit: **FotMob's `matchDetails` response — which
we already fetch for every match — carries far more than the team stats we currently
parse.** `laliga/scraper.py`'s `_parse_fotmob_stats()` only reads a handful of the
`content` block's keys. Confirmed present but **completely unparsed** right now
(checked live against fotmob-id 5868037, 2026-08-28 Racing Santander vs Elche):

- **Referee**: name, nationality, AND career disciplinary stats — cards/match, fouls/
  match, penalties awarded, sample size (`content.matchFacts.infoBox.Referee`, e.g.
  `{"text": "Carlos Muñiz Muñoz", "stats": [{"type":"yellowCards","value":3.6,
  "valueType":"perMatch",...}, {"type":"penalties","value":18,"valueType":"total"}]}`).
  This alone unlocks real "does this referee book more/give more penalties than
  average" analysis — no other source needed.
- **Weather**: temperature, wind speed+direction, humidity, precipitation, conditions
  (`content.weather`, e.g. `{"temperature":22,"windSpeed":5,"description":"Fair"}`).
- **Stadium detail**: exact capacity, lat/long, surface type (`infoBox.Stadium`) — we
  currently only keep a bare venue name/city string.
- **Attendance**: exact figure (`infoBox.Attendance`, e.g. `22496`) — our own
  `meta.attendance` field exists in the schema already but is usually `null` because
  nothing populates it from this source.
- **Player market value + age + nationality**: every lineup player carries
  `marketValue` (€), `age`, `countryName`/`countryCode` (`content.lineup.*Team.
  starters[].marketValue`, e.g. a keeper valued at €12.2M) — none of this is captured
  today; `build_players.py`'s player records have no age/nationality/value fields at
  all.
- **Player profile photos**: FotMob serves headshots at a predictable URL —
  `https://images.fotmob.com/image_resources/playerimages/<fotmob_player_id>.png`
  (confirmed live, 2026-09-15: returns 200 for a real player id). Not a data-capture
  gap so much as an ID-matching one: `build_players.py` keys every player by
  **WhoScored's** player id, and FotMob's lineup objects carry a *different* numeric
  id (`content.lineup.*Team.starters[].id`) with no shared key between the two — the
  same "no shared id across providers" problem the shot-matching and referee/market-
  value work above already ran into, just for players instead of shots. Matching would
  need to go by name (+ team, to disambiguate common surnames) the same best-effort way
  `_norm_player()`/`_pop_xg()` in `build_match_details.py` already match shots across
  providers — reuse that matching approach rather than inventing a new one. Once
  matched, this is a genuine UI win: player photos in the Players table, Match Centre
  lineups, and Player Lab, none of which show any image today.
- **Head-to-head history** (`content.h2h`: `summary` + `matches`), **momentum**
  (`content.momentum` — live match-swing data), **attacking zones**
  (`content.attackingZones`), **player of the match + top performers**
  (`matchFacts.playerOfTheMatch`, `topPlayers`) — all fetched, all discarded.

None of this needs a new scraper or a new source — it's a parsing gap in
`_parse_fotmob_stats()`/`build_match_json()`, not a data-availability gap. Cheapest,
highest-value next step of this whole plan.

### WhoScored event taxonomy — we use maybe a third of it

Full event-type inventory from a 40-match sample of 2025-26 (`type.displayName`,
counts across the sample — see this session's own audit): `Pass` (38365), `BallRecovery`
(3156), `BallTouch` (2755), `Aerial` (2040), `Foul` (1976), `Clearance` (1952),
`TakeOn` (1482), `Tackle` (1307), `CornerAwarded` (772), `Dispossessed` (627),
`Interception` (576), `BlockedPass` (575), `Challenge` (550), `SavedShot` (495),
`Save` (494), `KeeperPickup` (490), `SubstitutionOff/On` (374 each), `MissedShots`
(355), `Card` (169), `OffsideGiven`/`OffsidePass`/`OffsideProvoked` (130 each),
`FormationChange` (128), `Goal` (106), `FormationSet` (80), `KeeperSweeper` (62),
`Error` (54), `ShieldBallOpp` (52), `Claim` (50), `Punch` (36), `ShotOnPost` (22),
`PenaltyFaced` (13), `Smother` (8), `GoodSkill` (7), `ChanceMissed` (3).

Currently consumed: `Pass` (pass network, xA), shot types (xG/xGOT), `Card` (discipline
counts), `SubstitutionOn/Off` (minutes), `Goal` (timeline). **Not currently touched at
all**, despite being right there in every match:

- **Goalkeeper distribution/sweeping**: `KeeperSweeper`, `Claim`, `Punch`, `Smother`,
  `KeeperPickup`, `PenaltyFaced`, `Save` — a full goalkeeper-specific stat page
  (claims, sweeper actions, punches, save types) is buildable today with zero new
  scraping, and ties directly into the goals-prevented work in the companion plan.
- **Formation/tactics timeline**: `FormationChange`/`FormationSet` events carry the
  actual formation string and personnel at each change — a "formation used" /
  "in-match tactical shift" stat or visual doesn't exist anywhere in the dashboard yet.
- **Defensive duels beyond tackles**: `Aerial`, `Challenge`, `ShieldBallOpp`,
  `Dispossessed`, `BallRecovery` — richer defensive-duel-win-rate stats than the
  current flat tackle/interception counts.
- **Set-piece specifics**: `CornerAwarded` (772 events, unused) — corner-taker stats,
  corner conversion rate, near-post vs far-post delivery (derivable from the
  associated pass's end-coordinates) don't exist yet.
- **Individual errors**: `Error` event type (54 in the sample) — WhoScored explicitly
  tags "error leading to shot/goal" — a real, well-known stat (Opta's "Errors leading
  to goal") sitting unused.
- **`GoodSkill`**: literally a flagged "nutmeg/skill move" event — flair/entertainment
  stat, low analytical value but easy and fun (e.g. a Standouts "Most skill moves"
  card).

### Understat — roster-level fields beyond shots

Already confirmed this session: per-shot xG (used for the 3-source comparison), but
`_understat.rosters` (per-player: goals/assists/xG/xA/**time**/position, plus
`shotType`, `lastAction`, `player_assisted`, `xGChain`, `xGBuildup` on shots) is
fetched and stored but the roster block itself is never read by any builder —
`xGChain`/`xGBuildup` specifically (possession-chain xG credit, a lighter-weight
cousin of xT) are sitting there unused on every shot Understat has.

## Part 2 — Genuinely unavailable data, and how to get it

Filtered down hard after the Part 1 findings — most things that *look* unavailable
turn out to already be in FotMob's response. What's left is genuinely outside what
WhoScored/FotMob/Understat's public surfaces give us:

| Data | Why it's not available today | How to get it | Cost/feasibility |
|---|---|---|---|
| **Player tracking / positional data** (full 22-player x/y at 25fps, off-ball runs, pressing traps, distance covered, sprint speed) | None of our 3 sources are tracking providers — WhoScored/Opta and FotMob are event-based (ball-and-nearby-player only, at the moment of an action) | Paid providers only: SkillCorner, Second Spectrum, Opta's own tracking product, or Stats Perform. No free/scrapable tracking source exists for top-flight football. | High cost (enterprise pricing, typically not sold to individuals), realistically out of reach for this project unless a specific cheap/trial tier exists — worth a one-time check, not worth planning further without confirming pricing. |
| **Transfer history / contract data** | Not part of any match-scraping surface | Transfermarkt has a well-trodden (if fragile — frequent anti-scrape changes) scraping path; also has an unofficial API wrapper library (`transfermarkt-api` on PyPI/GitHub) other projects use | Free but fragile; a new scraper module (`transfermarkt.py`) following this project's existing pattern (own team-name matching layer, since Transfermarkt's naming will diverge from FotMob/WhoScored's the same way every other source has). Medium effort. |
| **Betting odds / market movement** | Not part of any current source | The Odds API (free tier, limited requests/month) or scraping Oddsportal/similar (fragile, same anti-bot concerns as WhoScored) | Free tier feasible for closing-line odds only (not live movement); a genuinely new integration, moderate effort. Useful for a "market-implied vs. our model's win probability" comparison feature. |
| **Injury history / fitness timeline** | Not part of any current source | Transfermarkt also publishes injury history per player; same scraping approach as transfer data above | Bundle with the transfer-data scraper above rather than a separate effort. |
| **VAR review detail** (what was reviewed, why, overturned or not) | FotMob's `matchFacts` surfaces incidents in the live commentary/timeline text, not as structured data; no source gives structured VAR decisions | Would require parsing FotMob's freetext commentary feed (`content.matchFacts.events`/`liveticker`) with pattern matching for VAR-related language — fragile, low confidence in completeness | Low priority; freetext-parsing approach is the only option and isn't reliable enough to build real stats on. |
| **Crowd sentiment / social reaction** | No source scraped | X/Twitter API (paid tiers only now), Reddit API (free, rate-limited) | Out of scope for a stats database — this is a different product entirely (sentiment analysis), not a football stat. Not recommended. |

**Bottom line**: after this audit, the only category worth real investment is
**tracking data** (if a feasible price point exists — worth one concrete pricing check
before ruling it out) and **Transfermarkt data** (transfers/injuries/market value —
though market value is *already* free via FotMob per Part 1, so Transfermarkt would
mainly add transfer history + injury timelines specifically). Betting odds is a nice-
to-have, not a stats-database core need. VAR and social/sentiment data are not worth
pursuing with current tooling.

## Suggested next step

Given the Part 1 findings are essentially free (data already fetched, just needs
parsing), the highest-value, lowest-cost move is: extend `_parse_fotmob_stats()` /
`build_match_json()` to capture referee, weather, stadium detail, attendance, and
player market-value/age/nationality into the match schema, then decide which of the
resulting stat ideas (referee bias, weather-adjusted stats, market-value efficiency,
goalkeeper distribution page, formation timeline, set-piece/corner stats, error-
leading-to-goal) to build UI for — likely worth its own follow-up planning pass once
this data is actually flowing, the same way `xg_core/PLAN_new_models.md` scoped the
shot-model side of things.
