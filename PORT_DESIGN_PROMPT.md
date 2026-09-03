# Port the "Broadcast Kinetic" redesign + platform fixes from XLALIGA

Paste this whole file as your first message to a fresh Claude Code session with its
working directory set to the target repo (XWORLDCUPTWIT, XEPL, or another dashboard).
It describes everything that changed in `XLALIGA` on branch `feat/dashboard-beta`
(merged to `main`) so the same work can be redone here, adapted to this repo's data.

Reference implementation: `https://github.com/RShiri/XLALIGA` — `laliga_dashboard/`.
If that repo is also on this machine, read its files directly instead of guessing;
they are the source of truth for every value below.

## 0. Before touching anything

1. Create and check out a new branch, e.g. `feat/dashboard-beta`. Never work on `main`
   directly. Check `git status` first — if there are uncommitted changes already in the
   working tree (there may be, e.g. a scraper WIP), stash them (`git stash push -u`) before
   branching, then pop the stash back after you check out the new branch.
2. Find this repo's dashboard folder (in XLALIGA it's `laliga_dashboard/`; the same codebase
   was forked for other sports, so look for a sibling folder with `app.js`, `match.js`,
   `styles.css`, `match.css`, `index.html`, `match.html`, `build_data.py`, `build_players.py`,
   `build_match_details.py`, `build_database.py`, `build_shots.py`, `build_site.py`).
   Confirm the file names match before assuming the structure is identical — adapt, don't force.
3. Check for `PROGRESS.md` at the repo root. If missing, create one modeled on XLALIGA's
   (a "What worked / what didn't" section + a scrape log table) — it's the running journal
   other sessions read before repeating a mistake.

## 1. Critical bug fixes (do these regardless of the visual work)

Look for the same class of bugs in this repo — they came from the same original codebase,
so they likely exist here too:

- **Per-team/per-player event files must be keyed by SEASON.** If there's a
  `build_player_lab.py` (or equivalent) writing one file per team across all scraped
  seasons, a player's stat totals silently sum every season ever scraped instead of the
  one on screen. Fix: key event files as `player_lab/<season>/<team>.js`
  (`window.LL_PLAYERLAB[season][team]`), and make the front end read `[season][team]`
  instead of `[team]`. Rebuild `player_lab/` from scratch each run so stale team-only
  files never linger.
- **No cache-busting `Date.now()` / `document.write` script injection.** If `index.html` /
  `match.html` inject scripts with a `?v=' + Date.now()` pattern, the browser can never
  cache them and every visit re-downloads everything. Replace with static `<script src=...>`
  tags and a content-hash cache-buster written at build time (see §3).
- **Team-colour collision.** If two teams/entities share a near-identical brand colour
  (e.g. two clubs both wearing red), anything colour-coded by side becomes unreadable.
  Add a ΔE (CIE76) collision guard in `match.js`: if the two primaries are within ~28 ΔE,
  fall back the second one to its secondary brand colour, then to a neutral. See XLALIGA
  `match.js` function `teamColours()` / `deltaE()` / `hexRgb()` / `rgbLab()` for the exact
  implementation to port verbatim (it's colour-math, not La Liga-specific).
- **PNG/image links 404 or are missing for bulk-scraped items.** If there's a renderer
  script (`renderer.py` or similar) that's normally invoked per-item (scrape+render+push)
  but there's also a separate bulk-scrape script that only saves raw JSON, anything scraped
  in bulk has no rendered image. Port `laliga/render_missing.py`'s pattern: a standalone
  script that renders every raw match/item JSON lacking an image (no browser needed) and
  publishes the result into the tracked, non-gitignored image folder the site links to
  (not the gitignored `output/` folder — a PNG that only lives there 404s on the live
  site). Wire it as the FIRST step of the rebuild pipeline (`REBUILD_STEPS` in `server.py`
  or equivalent), and make the build script (`build_data.py`) link the tracked/published
  copy, copying it there itself if only the gitignored copy exists.

## 2. Design system: "Broadcast Kinetic"

A live-sport broadcast-graphics identity: carbon ground, one signal colour, chamfered
plates, slanted controls, condensed italic display type, hatched (diagonal-striped) bars.
Read this whole section, then re-derive the exact token block for `styles.css` — don't
copy XLALIGA's La Liga branding (lime accent) blindly if this sport/brand calls for a
different single accent colour; ask the user which accent to use if it's not obvious
(e.g. keep gold for a World Cup property, or pick something that doesn't clash with this
sport's existing kit/team colours which appear on bars and chips).

### Tokens (`:root` in `styles.css`)

```css
:root {
  color-scheme: dark;
  --bg: #0c0d10;              /* carbon ground */
  --plate: #15171c;           /* card/panel surface */
  --plate-2: #1c1f26;         /* raised surface (headers, hovers) */
  --well: #090a0d;            /* recessed surface (bar tracks, code) */
  --line: rgba(255,255,255,0.08);
  --line-strong: rgba(255,255,255,0.18);
  --text: #f5f7fa;
  --muted: #b9bfc9;
  --muted-2: #737b88;

  --accent: #d7ff3a;          /* THE one signal colour — ahead/active/positive. Pick per-brand. */
  --accent-ink: #0c0d10;      /* text ON the accent fill */
  --accent-dim: #96b328;
  --accent-soft: rgba(215,255,58,0.12);
  --brand-red: #ff2a4d;       /* the only OTHER semantic colour — negative/relegation */
  --brand-red-soft: rgba(255,42,77,0.14);

  --positive: var(--accent); --negative: var(--brand-red);
  --info: #9fd0ff; --info-soft: rgba(159,208,255,0.08);
  --goal: var(--accent); --warn: #ffb020; --neutral: #4a505b;

  --radius: 0px;  /* NO border-radius anywhere in this skin */
  --plate-cut: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px));
  --slant: polygon(10px 0, 100% 0, calc(100% - 10px) 100%, 0 100%);       /* tabs, buttons, chips */
  --slant-sm: polygon(4px 0, 100% 0, calc(100% - 4px) 100%, 0 100%);      /* small controls, bar tracks */
  --hatch: repeating-linear-gradient(-55deg, rgba(255,255,255,0.28) 0 5px, rgba(255,255,255,0.12) 5px 9px);
  --hatch-lime: repeating-linear-gradient(-55deg, var(--accent) 0 5px, var(--accent-dim) 5px 9px);
  --hatch-red: repeating-linear-gradient(-55deg, var(--brand-red) 0 5px, #a11c34 5px 9px);

  --font-display: "Barlow Condensed", "Barlow", "Segoe UI", system-ui, sans-serif;
  --font-body: "Barlow", "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif;
  --font-label: "Barlow Condensed", "Barlow", sans-serif;
}
```

Load the fonts via Google Fonts `<link>` in both `index.html` and `match.html`:
`https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,600;0,700;0,800;1,700;1,800&family=Barlow:wght@400;500;600;700&display=swap`

### Rules to apply everywhere (not a partial pass — every component)

- **Body text weight 500, table text 600, entity names 700, condensed labels 700.**
  Don't ship the default 400 weight anywhere — it reads as thin at this letter-spacing.
- **`body` background**: carbon + a faint diagonal hatch (2px stripe, 14px repeat,
  `rgba(255,255,255,0.022)`), not a flat colour and not a grid.
- **Panels (`.card`, scoreboard, pitch wrap, lineup card, etc.)**: `background:
  linear-gradient(var(--plate-2), var(--plate) 48px); clip-path: var(--plate-cut);`
  — a 14px chamfer, no rounded corners, no drop shadow.
- **Tabs / buttons / chips / segmented controls**: `clip-path: var(--slant)` (or
  `--slant-sm` for small ones), active/selected state = solid accent fill with
  `color: var(--accent-ink)` and `font-style: italic`.
- **Section titles**: condensed italic 800 weight, uppercase, with a small skewed
  accent-coloured bar/slash before the text (`::before` transformed with `skewX(-18deg)`).
- **Every progress/comparison bar** (match stats, standings zone bars, probability bars,
  finishing deltas): the winning/positive side fills with `var(--hatch-lime)`, the
  other side with a neutral grey hatch, never a flat solid colour.
- **Form/result chips** (W/D/L or similar): slanted small plates (`--slant-sm`),
  win = accent fill + `--accent-ink` text, draw = neutral, loss = red fill + white text.
- **Header**: one row, wordmark in condensed italic 800 with the accent letter
  highlighted, tab strip sharing the row (don't add a duplicate status readout next to
  it — it steals space and a live "Scraper"/control button needs room too). Tighten tab
  padding/font-size below ~1280px; below ~1080px give the tab strip its own row under
  the wordmark rather than letting tabs get clipped. Test with any inject-a-button
  control panel present, not just the bare header.
- **Standings/table on phones**: keep rank + name columns `position: sticky; left: 0`
  with a scroll shadow, drop the least essential numeric columns under ~640px so the
  table doesn't force horizontal scroll of the whole page.
- **Fixture/results grid**: if a matchday/round has an even-ish number of items (e.g.
  ~10 for a 20-team league), lay the grid out `repeat(2, minmax(0,1fr))` (or a sensible
  fixed column count) instead of `auto-fill` with a small `minmax`, so a nearly-full
  matchday doesn't leave an odd number of orphaned cards in the last row.
- **Accessibility carried over from the earlier structural pass** (keep, don't regress):
  real `role="tab"`/`role="tabpanel"` ARIA wiring with roving tabindex and arrow-key
  navigation, `:focus-visible` rings on every interactive element,
  `prefers-reduced-motion` respected (disable/replace shimmer, sweep, and load
  animations), labels on every filter input (no placeholder-as-label), `<h1>` on both
  pages, tab + season state readable from the URL (`#<season-or-key>/<view>`).

### Match Centre specifics

- Scoreboard as a broadcast lower-third: chamfered plate, big italic score in the
  accent colour, crest + name either side, a matchday/date/venue meta line, goals
  timeline as two columns (home left, away right).
- A sticky section nav bar generated from the page's own block list (stats, shot map,
  pass network, lineups, etc.), current section underlined/filled in the accent colour.
- Match stat bars: winning side's number takes the accent colour; the bar itself uses
  the hatch pattern, not a flat fill, with a `stat.hBetter`/`stat.aBetter` flag driving
  which side is highlighted.
- Pitch markings: dark broadcast-green fill (`#0f3d22`-ish, not bright grass green),
  line strokes at ~35–45% white alpha.

## 3. Per-season (or per-tournament-edition) data split

If `app.js`/`match.js` currently load one monolithic `data.js` + `players.js` + `shots.js`
covering every season/edition at once, split it exactly like XLALIGA's `build_split.py`:

- New `data/index.js`: `window.LL_INDEX` (or this repo's namespace) with
  `{generated, v, defaultSeason, seasons: {key: {status, counts}}, teamColors}`.
  `v` is a short content hash of the season bundles — the cache-buster for everything
  loaded on demand.
- New `data/<season>.js` per season/edition: that season's slice of the standings/
  matches data + players + shots, all under one `<script>` tag.
- `index.html`/`match.html` load `data/index.js` statically; `app.js` fetches
  `data/<season>.js?v=<hash>` on demand when a season is selected (`loadSeason()`), and
  `match.js` fetches the season bundle it needs for head-to-head context the same way.
- Write this as a new build script (model it on `build_split.py`) and make it the LAST
  step of every rebuild pipeline (`REBUILD_STEPS`, `build_site.py`), after
  `build_data.py`/`build_players.py`/`build_shots.py`/`build_player_lab.py`.
- Update this repo's `CLAUDE.md` (or equivalent docs) to say the site loads per-season
  bundles now, not the monolithic files, and that forgetting the split script means the
  site silently keeps showing the previous build.

If this repo's structure is a single-season/single-edition site with no season switcher
(unlikely, but check), this step doesn't apply — skip it and say so.

## 4. In-browser PNG export (no server needed)

Port `laliga_dashboard/match_export.js` and its wiring into `match.js`/`match.html`
(see XLALIGA commits "Browser PNG: wide match board..." on `feat/dashboard-beta`):

- A `window.LL_EXPORT.render(M) -> Promise<canvas>` that draws a wide match/event board
  to a `<canvas>` in this repo's version of the skin: header/scoreboard, goal or event
  timeline, entity lineups + pass-network-style graphic (if the sport has one), a
  stats comparison panel, and shot-map-or-equivalent visuals — reuse whatever this
  repo's `matchRecord`/detail JSON already contains (don't invent new stats).
  `window.LL_EXPORT.download(canvas, filename)` saves it via `canvas.toBlob` + a
  synthetic `<a download>` click — no server round-trip.
- A "Download image" button in the match page's header area, wired in `match.js` next
  to (not replacing) any existing pipeline-rendered PNG link — label that one distinctly
  (e.g. "Pipeline PNG") since it may still be needed for social-post automation.
- This is what makes every match/event get a shareable image on the **static, deployed**
  site (GitHub Pages can't run Python), independent of whether the item was ever
  rendered by the offline pipeline.

## 5. Domain adaptation — read this before porting anything literally

- **XWORLDCUPTWIT / wc2026_dashboard**: this is a tournament (groups + knockout), not a
  round-robin league — keep its group tables and knockout bracket UI, just re-skin them
  in the tokens above. Its `styles.css` already has a `futuristic-theme.css` — check
  whether that's a competing/older theme attempt and decide whether to fold it in or
  retire it, don't just add a third stylesheet on top.
- **XEPL / epl_dashboard**: structurally near-identical to La Liga (round-robin league,
  same file names, same `hub.html`/`hub.css` may or may not exist — check). This is the
  most direct, lowest-risk port of the three. **Note:** at last check this repo was on
  branch `claude/scrape-24-25-season-xepl` with an uncommitted change to
  `epl/scrape_whoscored.py` — don't discard that; stash it, branch from wherever the
  user wants (confirm which base branch), and restore the stash after.
- **F1Visualized** (`https://rshiri.github.io/F1Visualized/`): not a football dashboard —
  do not assume any of the file names or data shapes above exist. Locate its repo first
  (it wasn't found in this session's usual project locations — ask the user for its
  local path or clone URL if you can't find it). Re-derive an analogous plan: what are
  this site's data-loading bottlenecks (if any), does it already theme consistently, is
  there a natural "signal colour" moment (fastest lap, race leader, DRS) that the accent
  colour + hatch-bar language could represent well, and is there a shareable single-race
  "image export" moment worth porting the browser-PNG idea to. Treat §2's *rules* (one
  accent, no radius, chamfered plates, condensed type, hatched comparison bars) as the
  transferable part; treat the *file names and DOM structure* in §2–4 as football-specific
  and not literally portable.

## 6. Process

1. Fix bugs (§1) first, commit separately, verify in a local server before moving on.
2. Apply the design tokens + component pass (§2) across every page/tab, not just one —
   screenshot each view at desktop and phone width and check contrast/overflow before
   calling it done.
3. Do the data split (§3) and image export (§4) if they apply to this repo, each as
   their own commit(s).
4. Update this repo's `CLAUDE.md`/docs and `PROGRESS.md` the same way XLALIGA's were
   updated — new gotchas, new build step, new file locations.
5. Push the branch, but only merge to `main` / trigger a deploy if the user explicitly
   asks — show them screenshots first the same way this session did for XLALIGA.
