# PROGRESS — XLALIGA

Running log of the La Liga pipeline: every scrape, every platform change, and the lessons
behind both. Newest entries first in each section.

Rows under **Scrape log** are appended **automatically** by `laliga/progress_log.py`, which is
called from `run_match.py`, `scrape_whoscored.py`, `backfill.py` and the dashboard's **Scraper**
button — every scrape lands here whether it was a scheduled task, a bulk backfill, or a click.
Write the Platform and Lessons sections by hand, from the dashboard's Scraper panel ("Log a
note"), or with:

```
py laliga/progress_log.py platform "what changed"
py laliga/progress_log.py lesson --worked "what to keep doing"
py laliga/progress_log.py lesson --failed "what not to repeat"
py laliga/progress_log.py show --limit 20
```

Sister project: **[XEPL](https://github.com/RShiri/XEPL)** keeps the same journal at its own
`PROGRESS.md`. The two codebases are twins — a lesson learned in one is nearly always true in
the other, so when you add an entry here, consider adding it there too.

## Platform updates & changes

<!-- progress:platform -->
- **2026-08-24** — 2026/27 promoted clubs: added colours for Deportivo A Coruña, Malaga and Racing Santander (plus the FotMob/WhoScored name variants). Crests still need 'py laliga/download_crests.py' on a machine that can reach FotMob's CDN.
- **2026-08-24** — 26/27 readiness sweep: scraper.fotmob_fetch_wc_matches migrated off the retired live-only endpoint to /api/data/matches (legacy XML only if the new one returns nothing); defaultSeason now follows the newest PLAYED season instead of being pinned to 2025-26; backfill/scrape_whoscored --season defaults to the newest schedule on disk; git_ops finally pushes shots.js + player_lab/ and finds the raw JSON under matches/<season>/; build_schedule warns about clubs with no colour or crest.
- **2026-08-24** — FotMob moved the fixture feed: api.fotmob.com/matches?date= is now LIVE-ONLY (root <live>/<exmatches>, ?date ignored) — which is why 2026-27 came back empty on every date. The spine now uses www.fotmob.com/api/data/leagues?id=87&season=2026%2F2027 (whole season in one request, with round numbers), with /api/data/matches?date= as the per-day fallback and the old XML as a last resort.
- **2026-08-23** — build_schedule is now incremental by default: it sweeps from a few days before the last finished match to a fortnight ahead and merges into the existing schedule (--full for the whole season). It also parses the feed as XML or JSON, and says why a sweep found nothing instead of silently reporting 0.
- **2026-08-22** — Scraper panel collapsed to a single '⚡ Update everything' button — picks the season, refreshes fixtures, scrapes what's missing, rebuilds and publishes in one click; the season/action/id/limit controls moved behind an Advanced toggle.
- **2026-08-22** — Scraper panel: added a Commit & push button, made publish-when-done the default, and folded the fixture refresh into the scrape action (optional step — a FotMob outage no longer blocks the scrape). All pushes now go through the local clone's remote instead of git_ops.
- **2026-08-22** — Added this journal (`PROGRESS.md` + `laliga/progress_log.py`) and a
  **Scraper button** in the dashboard, served by the local control API in `server.py`
  (`py server.py` → http://localhost:8778/laliga_dashboard/index.html). The button runs the
  same scrape/rebuild commands as the CLI and writes its result here automatically.
- **2026-07-30** — Match page: calibrated in-play win-probability chart.
- **2026-07-07** — Wired the **v3 (23-feature) xG** + retrained xA into both the dashboard and
  the PNGs; added seasons **2022/23 and 2023/24** (schedule spine + rich WhoScored layer), so
  four full seasons are live (1520 match-centre pages).
- **2026-07-07** — Premium dark/gold theme; Player Lab layout aligned with XEPL.
- **2026-07-04** — Added `.nojekyll` so GitHub Pages serves the static dashboard directly.
- **2026-07-04** — Premier League code extracted out of this repo into standalone
  **RShiri/XEPL**; La Liga stays here.
- **2026-07-03** — `xg_core/` became the canonical model home (no hard-coded coefficients);
  copies vendored into XWORLDCUPTWIT and BCNPROJECT so all three stay identical.
- **2026-07-02** — Render hook now also refreshes `shots.js` + `player_lab/` (26/27-ready).

## Lessons — what worked / what didn't

<!-- progress:lessons -->
- ✅ **Worked** — Matchday inference now tries the payload order AND kickoff order, validating each by 'no team twice in a round'. FotMob's season view is date-ordered, not round-ordered, so the first attempt fails and the second succeeds — and if both fail it leaves matchday empty instead of publishing a wrong table.  (2026-08-24)
- ❌ **Didn't work** — A WhoScored failure used to be invisible and permanent: the match still saved (FotMob shots only, no event stream), so _already_scraped counted it done and no later run would ever fill in the maps/lineups. backfill now classifies each match none/partial/full and --redo-partial retries only the partials.  (2026-08-24)
- ✅ **Worked** — Probing candidate endpoints from the user's own machine (--probe-endpoints) found the replacement in one shot: /api/matches 404s but /api/data/matches works, and /api/data/leagues returns the entire season. When a source dies, enumerate doors rather than guessing one.  (2026-08-24)
- ❌ **Didn't work** — A sweep printing '0 matches so far' told us nothing: ET.fromstring failures were swallowed by 'except Exception: continue', so a FotMob format change looked identical to 'no fixtures yet'. Failure counters + a verdict line now distinguish blocked / format-changed / no-such-league / not-published.  (2026-08-23)
- ❌ **Didn't work** _deploy_ — `git_ops.push_match_update` copies `data.js`, `players.js`,
  `matches_detail/` and `database/` but **not** `shots.js` or `player_lab/`, so auto-pushed
  matches leave the live Team Lab and Player Lab stale even though the renderer regenerates
  them locally.  (2026-08-22)
- ❌ **Didn't work** _seasons_ — `defaultSeason` is pinned to `"2025-26"` in
  `laliga_dashboard/build_data.py`, and `build_database.py` exports only that default season.
  A new season goes live in the switcher but the site still lands on the old one.  (2026-08-22)
- ✅ **Worked** _scraping_ — when undetected-chromedriver broke on **Chrome 149**
  (`SessionNotCreatedException`), falling back to plain Selenium via Selenium Manager fixed it.
  The fallback catches `except Exception`, not just `ImportError` — keep it that way.  (2026-08-22)
- ❌ **Didn't work** _team matching_ — `scrape_whoscored._key` stripping "real" collapsed
  "Real Madrid" → "madrid", which substring-matched "atletico**madrid**" and **scrambled the
  two Madrid clubs' fixtures**. Keep "real"; verify the mapping is collision-free before any
  bulk re-scrape.  (2026-08-22)
- ✅ **Worked** _data integrity_ — FotMob's historical feed sometimes reports a real result as
  0-0 (2 in 2023/24, 6 in 2024/25). Preferring the scraped WhoScored fulltime score over the
  schedule score in `build_data.py` fixed the standings and made a stale schedule spine
  harmless.  (2026-08-22)
- ❌ **Didn't work** _sources_ — Understat moved to AJAX loading, so the old `JSON.parse`
  blob scrape returns nothing. WhoScored is the bulk source; `laliga/understat.py` still needs
  updating for the new structure.  (2026-08-22)
- ❌ **Didn't work** _publishing_ — publishing PNGs to `LaLiga/` aliased the `laliga/` code
  folder on a case-insensitive filesystem. The publish dir is **`laliga_png/`**
  (env `LALIGA_PNG_SUBDIR`).  (2026-08-22)
- ❌ **Didn't work** _rebuilds_ — `tools/regen_unified.py` can't feed the pass-level xA model
  (derived files lack full pass qualifiers). Use the canonical `laliga_dashboard/build_*.py`
  builders, and point `LALIGA_MATCH_DIR` at a copy that actually has the raw scrapes.  (2026-08-22)
- ❌ **Didn't work** _front-end_ — `players.js` fields are `g`/`a`/`xg`/`mp`, not
  `goals`/`assists`; reading the long names showed 0 for every player.  (2026-08-22)
- ✅ **Worked** _repo size_ — keeping raw match JSONs gitignored (~2 MB each, 769 MB/season)
  and shipping only the derived `matches_detail/*.js` (~74 MB) keeps the repo clonable.  (2026-08-22)

## Scrape log

<!-- progress:scrapes -->
| When | Season | Trigger | Target | Result | Took | Notes |
|---|---|---|---|---|---|---|
| 2026-07-07 | 2022-23 | bulk backfill (historic) | full season · 380 matches | ✅ 380 saved | — | Archived-season WhoScored path; 583 players |
| 2026-07-07 | 2023-24 | bulk backfill (historic) | full season · 380 matches | ✅ 380 saved | — | Archived-season WhoScored path; 598 players |
| 2026-07-04 | 2024-25 | bulk backfill (historic) | full season · 380 matches | ✅ 380 saved | — | Archived-season WhoScored path; 589 players |
| 2026-07-01 | 2025-26 | bulk backfill (historic) | full season · 380 matches | ✅ 380 saved | — | 600 players; the default season |
