# Prompt: add player profile photos to XLALIGA and XEPL

Paste this into a **fresh Claude Code session** opened at
`C:\Users\puzik\OneDrive\שולחן העבודה\XLALIGA` (it touches both XLALIGA and its sibling
`XEPL` at `C:\Users\puzik\OneDrive\שולחן העבודה\XEPL` — same fix, same code shape, do
both in one pass rather than two separate sessions). Read `CLAUDE.md` (XLALIGA) and
`XEPL\CLAUDE.md` first for full project context if either has drifted since this was
written (2026-09-15).

## Context

- Confirmed live, 2026-09-15: FotMob serves player headshots at a predictable URL —
  `https://images.fotmob.com/image_resources/playerimages/<fotmob_player_id>.png`
  (tested with a real id, returned HTTP 200). Free, no token, no extra scraping —
  the FotMob player id is already fetched into `content.lineup.*Team.starters[].id`
  in every match's `matchDetails` response (`laliga/scraper.py`'s
  `fotmob_fetch_match_details`/`_parse_fotmob_lineup`).
- **The only real problem: no shared player id across providers.**
  `build_players.py` keys every player record by **WhoScored's** `playerId` (the
  primary event-stream source), which has no relationship to FotMob's numeric player
  id. This is the exact same "provider ids don't line up" problem this session already
  solved twice — once for shots (`build_match_details.py`'s `_norm_player()` +
  `_pop_xg()`, matching by team + surname + minute) and once for referee/weather/
  market-value data (see `DATA_AUDIT_AND_STATS_PLAN.md`). **Reuse the shot-matching
  approach's spirit, not its exact code** — players match by team + normalized name
  (no minute involved, since a player isn't a per-event thing), so the matching key is
  simpler: `(team, _norm_player(name))` should be enough per match, with a fallback to
  fuzzy/substring matching for cases where FotMob and WhoScored spell a name
  differently (accents, "Bill" vs "William", suffix jersey nicknames, etc. — check
  what surprises come up in practice rather than assuming ASCII-normalized surnames
  always agree).
- `DATA_AUDIT_AND_STATS_PLAN.md` (XLALIGA repo root) has this flagged under Part 1 —
  read that entry for the exact evidence trail; this prompt is the "go build it now"
  follow-up to that plan.

## What to actually do

1. **Capture FotMob's player id → photo URL per match.** In `laliga/scraper.py`
   (mirror in `epl/scraper.py` — check first whether these have diverged since this
   was written; if they have, apply the fix to each independently rather than
   assuming they're identical), extend whatever already reads
   `content.lineup.*Team.starters[]`/`subs[]` to also capture `id` per player (it's
   already parsed for lineup purposes — check `_parse_fotmob_lineup`, this may just be
   a matter of not discarding a field that's already in hand). Store a
   `fotmob_id → photo_url` (or just `fotmob_id`, deriving the URL string at read time)
   somewhere in the raw match JSON — a new `_fotmob_player_ids` block is probably the
   cleanest, mirroring `_fotmob_shots`' pattern from today's session (a sibling
   top-level key, not merged into `events`).
2. **Match to our own (WhoScored-keyed) player records.** In `build_players.py`,
   build a per-match `(team, normalized_name) → fotmob_player_id` lookup (same shape
   as `_cross_provider_xg_buckets` in `build_match_details.py`, adapted for players
   instead of shots) and attach a `photo` URL field to each player record in
   `_new_player()`/`aggregate()`. A player who never matches keeps `photo: null` — no
   image is a normal, expected outcome for some players (name-matching won't be
   perfect), not a bug to chase to 100%.
3. **Backfill existing raw match data.** This needs the raw `matchDetails` re-fetched
   per match to pick up the player id (same pattern as this session's
   `_fotmob_shot_xg_list` backfill script — write a similar one-off script, don't hand-
   edit JSON files). Both XLALIGA's and XEPL's `laliga/matches/`/`epl/matches/`
   corpora need this backfill across all already-scraped seasons — this is a lot of
   matches (XLALIGA: ~1900+, XEPL: ~1900+ per today's session), so budget real time
   for it (a plain HTTP fetch per match, no browser — should be much faster than a
   WhoScored scrape, more like the FotMob-only backfill from earlier today which did
   ~50 matches in under a minute; scale that up, watch for the rate-limit/anti-bot
   behavior FotMob's endpoint has shown before if you push very high concurrency).
4. **UI**: wire the photo into
   - Players table (`app.js` — a small headshot next to the name, matching the
     existing crest-next-to-name pattern already there for teams).
   - Match Centre lineups (`match.js` — the lineup cards currently show shirt number +
     name only).
   - Player Lab (if it has a player-header area — check current structure first).
   Fall back gracefully (no broken-image icon) when `photo` is null — a CSS
   `background-color` placeholder or simply omitting the `<img>` tag entirely both
   work; pick whichever is less code.
5. **Rebuild + verify in browser** for both XLALIGA and XEPL: photos actually render
   for players who matched, no broken-image glyphs for players who didn't, zero
   console errors. Spot-check a handful of players by eye against FotMob's own site to
   confirm the matched photo is actually the right person (a wrong name-match could
   silently attach the wrong photo — this is worth actually checking, not just
   confirming *an* image loaded).

## Guardrails

- Never hotlink-embed-and-forget: confirm `images.fotmob.com` is comfortable being
  hotlinked at this volume (no robots.txt/ToS check was done as part of writing this
  prompt) before shipping to the live site — if there's any doubt, downloading and
  serving a cached copy from `team_logos`-style local storage is the safer fallback,
  mirroring how crests are already handled locally rather than hotlinked.
- Match confidence matters more than match coverage here — a wrong photo is worse than
  a missing one. Don't loosen the name-matching threshold just to raise the "% of
  players with a photo" number.
- Don't touch anything about the shot-xG-comparison or referee/weather/market-value
  work from `DATA_AUDIT_AND_STATS_PLAN.md` unless you're also asked to — this prompt
  is scoped to photos only.
- Same push discipline as everything else in these repos this session: commit as you
  go, but don't push to `main` without the user's go-ahead in the moment (both repos'
  `main` currently reflect a fully working, verified state — don't leave either one
  mid-broken-rebuild if you get interrupted).
