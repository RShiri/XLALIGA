# Port the "Broadcast Kinetic" redesign to F1Visualized

Paste this whole file as your first message to a fresh Claude Code session with its
working directory set to a local clone of `https://github.com/RShiri/F1Visualized`
(default branch: `claude/f1-pipeline-video-animation-d48zz5` — confirm with the user
which branch to base a new branch off, main may not be the active one). This repo was
not found on the user's machine when this prompt was written, so clone it first:
`git clone https://github.com/RShiri/F1Visualized.git`.

Reference implementation for the design language: `https://github.com/RShiri/XLALIGA`,
branch `feat/dashboard-beta` (merged to `main`), folder `laliga_dashboard/`. Read its
`styles.css` and `match.css` directly if that repo is also available locally — they are
the source of truth for the exact token values and CSS patterns below.

## 0. Scope — read this first

F1Visualized is **not** built like the football dashboards (XLALIGA/XWORLDCUPTWIT/XEPL).
It has no Python build pipeline for the front end, no per-match detail files, no
player_lab. The whole site is:

```
web/index.html          one page, five tabs (Overview, Standings, Calendar, Results, Stats)
web/assets/app.js        ~30KB vanilla JS, fetches web/data/<year>.json, renders everything
web/assets/styles.css    ~16KB, current theme below
web/assets/flags/*.svg   country flags, inline via <img>
web/data/2022.json … 2026.json   one file per season, already split (nothing to do here)
```

The repo root also has `animator.py`, `race_animator.py`, `combine_gp_videos.py`,
`fetch_season.py`, `scraper.py`, `config.py` and a `.github/workflows/f1_latest_video.yml`
— these generate race-replay **videos** for social posting. **Out of scope.** Don't touch
them; this port is about `web/` only, same as the football work only touched
`laliga_dashboard/`, not `laliga/scraper.py`.

## 1. Current state (so you know what you're changing, not guessing)

`web/assets/styles.css` today:
```css
:root {
  --bg: #0e0e13; --bg-2: #15151c; --panel: #1a1a22; --panel-2: #1f1f29;
  --border: #2a2a35; --border-2: #34343f;
  --ink: #f4f4f7; --ink-2: #b7b7c2; --ink-3: #82828f;
  --red: #e10600; --red-2: #ff2d24; --good: #22c55e;
  --radius: 14px;
  --shadow: 0 8px 24px rgba(0, 0, 0, .35);
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
```
Rounded cards (`--radius: 14px`), a pill-shaped season toggle, plain sticky tabs with a
bottom-border active state, system font stack — the same "flat dark mode, default
system font" starting point the football dashboards had before this pass. `web/assets/app.js`
already keeps a `TEAM_COLORS` map (McLaren orange, Ferrari red, Red Bull blue, etc.) used
as swatches next to driver/constructor names — **keep this map and this pattern**, it's
the equivalent of football's kit colours and should stay team-specific, not be replaced
by the one signal accent.

`web/index.html` structure to re-skin (don't restructure it, just re-theme it — the
five-tab IA is sound):
```
header.topbar   → brand mark "F1" + "VISUALIZED", season pill-toggle (2022-2026), "updated" timestamp
nav.tabs        → Overview / Standings / Calendar / Results / Stats
main
  #overview     → hero-grid: driver leader card, constructor leader card, next race, last race
                  + overview-lower: Top 5 Drivers mini-table, Top 5 Constructors mini-table
  #standings    → two-column driver + constructor championship tables
  #calendar     → calendar-grid of race cards
  #results      → a <select> to pick a race + a rendered race detail (podium, laps, etc.)
  #stats        → season stats table (avg finish, avg grid, etc.)
footer.site-footer
```

## 2. Bug fix first: the data fetch disables caching entirely

`app.js` loads season data with `fetch(\`data/${y}.json\`, { cache: "no-store" })` — this
is worse than a missing cache-buster: it forces the browser to hit the network on
**every single load**, even if the file hasn't changed, for a file that's 150–165KB.
Fix it the same way the football sites were fixed: normal caching (drop `cache:
"no-store"`, or set `cache: "reload"` only for the currently-selected season, not every
season) plus a content-hash or `lastUpdated`-derived version query string
(`data/${y}.json?v=<hash>`) so a real update still busts a stale cache without
disabling caching altogether. If there's a small `updated`/`lastUpdated` field already in
each season JSON, that's the simplest version string — don't add a build step for a
static-JSON site that doesn't have one.

## 3. Design system: "Broadcast Kinetic" for F1

Same rules as the football port, translated to motorsport, which this aesthetic suits
even more naturally than football (telemetry HUDs, timing towers, broadcast lower-thirds
are already this visual language). **Do not just copy the lime accent from XLALIGA** —
propose F1-specific accent options to the user and let them pick, or use your judgement
and say what you chose and why. Two strong, on-brand options:

- **Purple** (`#9d4dff`-ish) — F1's own "fastest lap" / purple-sector colour. Every F1
  fan already reads purple as "the best time set". Using it as the single site-wide
  signal colour (leader, active tab, "ahead" in any comparison) is a genuinely native
  choice, not a borrowed one, and it stays clearly distinct from every team's own colour
  and from red.
- **A cleaner red-orange** if you want to keep the existing brand-red identity but move
  it fully into the "signal" role (currently `--red` is both the brand mark AND used as
  a semantic warning-ish colour in a couple of places like `.nc-countdown b` — that's a
  collision worth resolving either way: pick ONE role for red and don't reuse it for two
  meanings).

Whichever you pick, keep it to one accent used consistently, and keep team colours
(the `TEAM_COLORS` map) doing what they already do — decorating driver/constructor rows,
never used as the site's structural accent.

### Tokens (replace the `:root` block above)

```css
:root {
  color-scheme: dark;
  --bg: #0c0d10;
  --plate: #15171c;
  --plate-2: #1c1f26;
  --well: #090a0d;
  --line: rgba(255,255,255,0.08);
  --line-strong: rgba(255,255,255,0.18);
  --text: #f5f7fa;
  --muted: #b9bfc9;
  --muted-2: #737b88;

  --accent: #9d4dff;            /* or your chosen F1 signal colour — see above */
  --accent-ink: #0c0d10;        /* or #fff if the accent is dark enough to need it */
  --accent-soft: rgba(157,77,255,0.14);
  --danger: #ff2a4d;             /* DNF, red flag, penalty — separate from the accent */
  --danger-soft: rgba(255,42,77,0.14);
  --good: #3dffb0;               /* personal-best green, position gained */

  --radius: 0px;  /* no border-radius in this skin — chamfers instead */
  --plate-cut: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px));
  --slant: polygon(10px 0, 100% 0, calc(100% - 10px) 100%, 0 100%);
  --slant-sm: polygon(4px 0, 100% 0, calc(100% - 4px) 100%, 0 100%);
  --hatch: repeating-linear-gradient(-55deg, rgba(255,255,255,0.28) 0 5px, rgba(255,255,255,0.12) 5px 9px);
  --hatch-accent: repeating-linear-gradient(-55deg, var(--accent) 0 5px, color-mix(in srgb, var(--accent) 55%, #000) 5px 9px);

  --font-display: "Barlow Condensed", "Barlow", "Segoe UI", system-ui, sans-serif;
  --font-body: "Barlow", "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif;
}
```
Load fonts in `web/index.html`'s `<head>`:
`https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,600;0,700;0,800;1,700;1,800&family=Barlow:wght@400;500;600;700&display=swap`

### Component pass — go through every one of the five tabs, not just Overview

- **`.card`** → chamfered plate (`clip-path: var(--plate-cut)`), no radius, no soft
  shadow-only depth — `background: linear-gradient(var(--plate-2), var(--plate) 48px)`.
- **`.topbar` season toggle** → currently a rounded pill; make it slanted chips
  (`--slant-sm`) instead, active season = solid accent fill + `var(--accent-ink)` text +
  italic. Same treatment for `nav.tabs` buttons — right now the active tab is just a
  red bottom border; give it a full accent-filled slanted tab like the football header.
- **`.leader-card`** (driver/constructor leader on Overview) → this is F1Visualized's
  equivalent of the "Projected Champion" panel that got the full broadcast-lower-third
  treatment in football: chamfered plate, a slanted accent label tab ("DRIVERS' LEADER"),
  big condensed-italic name, points in the accent colour, maybe a thin accent progress
  bar showing points gap to 2nd. Don't over-build it beyond what real data supports —
  check what fields the leader card already computes from the season JSON before adding
  new ones.
- **Standings tables** → position-change indicators (if the data has previous-round
  position) become small accent/danger up/down marks, not default green/red — keep
  `--good`/`--danger` for that, reserve the single accent for "current leader" emphasis
  and active-state chips, not for every table row.
- **Any bar/comparison visual** (points gaps, avg-finish comparisons in Stats) → hatched
  fill in the accent colour for the leading value, neutral grey hatch otherwise — same
  `--hatch`/`--hatch-accent` pattern as football's match-stat bars.
- **Podium display in Results** → this is the moment worth the most polish: three
  chamfered plates or a stepped podium shape, P1/P2/P3 in condensed italic, team colour
  swatch kept per-driver, fastest-lap driver called out with a small accent tag if that
  data exists.
- **Typography** → condensed italic 800 for the brand mark, tab labels, leader-card
  names, podium names; Barlow body weight 500 for everything else, table text 600. Don't
  ship the default system-font stack anywhere once this lands.
- Keep the same accessibility bar as the football pass: `:focus-visible` rings,
  `prefers-reduced-motion` respected on the `.tab-panel` fade and anything else animated,
  labelled form controls (the `#raceSelect` needs a `<label>`, not just `aria-label` if
  it doesn't already have real markup for it — check).

## 4. In-browser "Download image" export — a strong fit here

Football's `match_export.js` (drawing a shareable match board to a `<canvas>` and saving
it via `canvas.toBlob`, no server involved) translates directly and is arguably an even
better fit for F1: a **race result card** — podium, fastest lap, pole, points gained per
driver — is exactly the kind of thing people screenshot and share after a Grand Prix.
Build the equivalent here:

- `web/assets/race_export.js` (or fold into `app.js` if the codebase is small enough
  that a second file adds more overhead than it saves): `renderRaceCard(raceData) ->
  canvas`, drawing the chamfered-plate broadcast look — race name + round + flag,
  podium (P1/P2/P3 with team colours), fastest lap, and maybe the points table delta —
  from whatever the `#results` tab already has computed, don't invent new stats.
- A "Download image" button next to the `#raceSelect` control in the Results tab.
- Reasonable follow-up (not required for v1): the same idea for a **standings
  snapshot** (top-10 drivers/constructors as of the selected season) exported from the
  Standings tab.

## 5. Process

1. Fix the `cache: "no-store"` issue first (§2), commit it separately, verify in a local
   server.
2. Confirm the accent colour choice with the user (or make the call and say why) before
   writing the full token block — this decision shapes every component after it.
3. Re-skin all five tabs with the token + component pass (§3). Screenshot each tab at
   desktop and phone width (this site has no responsive breakpoints worth assuming exist
   — check `styles.css` for any `@media` blocks and adapt/add them, don't assume the
   current layout already collapses cleanly on a phone).
4. Build the race-card export (§4) as its own commit.
5. Do **not** touch `animator.py`, `race_animator.py`, `combine_gp_videos.py`,
   `scraper.py`, `fetch_season.py`, `config.py`, or the video GitHub Action — none of
   that is in scope for a dashboard redesign.
6. This repo has no `PROGRESS.md`. Create one at the root modeled on XLALIGA's (a "what
   worked / what didn't" section is enough here; there's no scrape-log table need unless
   `scraper.py` already writes one elsewhere).
7. Push the branch. Only merge / let the deploy workflow run if the user explicitly asks
   — show screenshots first.
