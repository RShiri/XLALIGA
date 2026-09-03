"""
Understat source for La Liga — xG / shots / PPDA / deep completions + shot-level xG.

Understat used to embed its data as page blobs (``var X = JSON.parse('...')``), but as of
2026-07 the site serves them via AJAX instead: league pages carry no blob at all, and match
pages keep only ``match_info``. Everything now comes from two JSON endpoints:

    GET /getLeagueData/<League Name>/<startYear>   e.g. "La liga/2025" (space, not
        underscore; startYear = season's first calendar year) -> {teams, players, dates}.
        ``dates`` lists every match in the season (played and upcoming) with per-side
        goals + xG — this alone is enough for season/team-level xG comparisons.
    GET /getMatchData/<understat_match_id>          -> {rosters, shots} — shots.h/.a and
        rosters.h/.a are keyed exactly like the old blobs (minute/X/Y/xG/player/result/...
        per shot; goals/xG/xA/time/position per roster entry), plus extra fields
        (shotType, lastAction, player_assisted, xGChain, xGBuildup, ...).

Raw HTTP to either endpoint is bot-blocked (returns an 18 KB shell). What works: load any
understat.com page in Selenium first (cookies/JS challenge), then run a same-origin
``fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})`` via
``execute_async_script`` — no token, ~0.3s/request. We reuse the WhoScored scraper's driver
when handed one (serialised by the run-lock); otherwise we spin up our own per call.

What we return (``understat_fetch_match_details``) is a **FotMob-shaped** dict so the
scraper's existing ``_parse_fotmob_*`` consumers can merge it alongside FotMob/WhoScored,
plus a ``_understat`` block carrying shot-level data (per-shot xG + coords + player) and
per-player roster stats (goals/assists/xG/xA/minutes) for the dashboard.

Season note: Understat keys a season by its START year — 2025 = 2025/26, 2026 = 2026/27.
"""
from __future__ import annotations

import re
import json
import time
import logging
import unicodedata

log = logging.getLogger("laliga.understat")

UNDERSTAT = "https://understat.com"
LEAGUE_SLUG = "La_liga"     # legacy page-path slug (kept for reference/back-compat)
LEAGUE_NAME = "La liga"     # AJAX endpoint's league name (note the space)


# ── team-name matching across feeds ───────────────────────────────────────────
def _key(name: str) -> str:
    """Accent-fold + strip club-suffix noise so 'Atlético Madrid' == 'Atletico Madrid'
    and 'Real Betis' matches Understat's 'Betis'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    for junk in ("cf ", "cd ", "rcd ", "ud ", "sd ", "deportivo ", " balompie", " club", "real ", "fc "):
        s = s.replace(junk, " ")
    return re.sub(r"[^a-z0-9]", "", s)


# ── Selenium driver (reuse scraper's if handed one) ───────────────────────────
def _new_driver():
    try:
        import undetected_chromedriver as uc
        opts = uc.ChromeOptions()
        opts.add_argument("--window-size=1920,1080")
        return uc.Chrome(options=opts, use_subprocess=True)
    except Exception as exc:
        log.info("undetected_chromedriver unavailable (%s); falling back to selenium", exc)
        from selenium import webdriver
        opts = webdriver.ChromeOptions()
        opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        d = webdriver.Chrome(options=opts)
        d.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        return d


_FETCH_JS = """
const url = arguments[0];
const cb = arguments[arguments.length - 1];
fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
  .then(r => r.text())
  .then(t => cb({ok: true, body: t}))
  .catch(e => cb({ok: false, err: String(e)}));
"""


def _fetch_ajax(path: str, driver=None) -> dict | list | None:
    """GET understat.com<path> as JSON via an in-page fetch (bypasses the raw-HTTP bot
    block). `path` starts with '/'. Spins up its own driver if none is given."""
    own = driver is None
    d = driver or _new_driver()
    try:
        if not (d.current_url or "").startswith(UNDERSTAT):
            d.get(UNDERSTAT + "/")
            time.sleep(2.5)
        d.set_script_timeout(20)
        res = d.execute_async_script(_FETCH_JS, UNDERSTAT + path)
        if not res or not res.get("ok"):
            log.warning("Understat AJAX %s failed: %s", path, (res or {}).get("err"))
            return None
        try:
            return json.loads(res["body"])
        except Exception:
            log.warning("Understat AJAX %s: non-JSON body (%d bytes)", path, len(res.get("body", "")))
            return None
    finally:
        if own:
            try:
                d.quit()
            except Exception:
                pass


# ── league data (cached per season within a process run) ──────────────────────
_league_cache: dict[str, dict] = {}


def get_league_data(season: str, driver=None) -> dict | None:
    """{teams, players, dates} for a season ('2025-26' style). `dates` covers every
    fixture (played + upcoming) with per-side goals + xG. Cached per season/process."""
    if season in _league_cache:
        return _league_cache[season]
    start_year = season.split("-")[0]
    data = _fetch_ajax(f"/getLeagueData/{LEAGUE_NAME}/{start_year}", driver)
    if not isinstance(data, dict) or not data.get("dates"):
        log.warning("Understat: no dates for %s %s", LEAGUE_NAME, start_year)
        return None
    _league_cache[season] = data
    return data


# ── match discovery ───────────────────────────────────────────────────────────
def find_understat_match_id(home: str, away: str, date: str | None,
                            season: str, driver=None) -> str | None:
    """Find an Understat match id for home/away (+ optional YYYY-MM-DD) in a season.
    ``season`` is '2025-26' style; Understat uses the start year (2025)."""
    league = get_league_data(season, driver)
    dates = league.get("dates") if league else None
    if not dates:
        return None
    hk, ak = _key(home), _key(away)
    best = None
    for m in dates:
        try:
            mh, ma = _key(m["h"]["title"]), _key(m["a"]["title"])
        except Exception:
            continue
        if not (mh == hk or hk in mh or mh in hk):
            continue
        if not (ma == ak or ak in ma or ma in ak):
            continue
        if date and (m.get("datetime", "")[:10] != date):
            best = best or m.get("id")   # remember a team-match even if the date differs
            continue
        return m.get("id")
    return best


# ── match fetch → FotMob-shaped stats + shot-level data ───────────────────────
def _num(v, cast=float):
    try:
        return cast(v)
    except Exception:
        return None


def fetch_understat_match(match_id: str, driver=None) -> dict | None:
    """Return {shots: {h, a}, rosters: {h, a}} for a match id, straight from
    getMatchData (both already keyed by side, same shape the old page blobs used)."""
    data = _fetch_ajax(f"/getMatchData/{match_id}", driver)
    if not isinstance(data, dict) or not data.get("shots"):
        log.warning("Understat: getMatchData empty for %s", match_id)
        return None
    return {"shots": data.get("shots"), "rosters": data.get("rosters")}


def understat_fetch_match_details(home: str, away: str, date: str | None,
                                  season: str, driver=None, match_id: str | None = None) -> dict | None:
    """FotMob-shaped stats dict (so the scraper's parsers/merge consume it) + a `_understat`
    block with shot-level + roster data. Returns None if the match can't be found/parsed."""
    league = get_league_data(season, driver)
    if not league:
        return None
    if match_id is None:
        match_id = find_understat_match_id(home, away, date, season, driver)
    if not match_id:
        return None
    info = next((m for m in league["dates"] if str(m.get("id")) == str(match_id)), None)
    if not info:
        return None
    matched = fetch_understat_match(match_id, driver)

    # Map Understat's league-row → the same match_stats keys the scraper/dashboard use.
    # (Shot/deep/PPDA breakdowns aren't in the league row; only xG/goals are — the rest
    # would need per-match getMatchData aggregation, which callers don't currently need.)
    ms = {
        "xg_home": _num(info.get("xG", {}).get("h")), "xg_away": _num(info.get("xG", {}).get("a")),
    }
    return {
        "_source": "understat",
        "match_id": match_id,
        "home_name": info.get("h", {}).get("title", home),
        "away_name": info.get("a", {}).get("title", away),
        "score": [_num(info.get("goals", {}).get("h"), int), _num(info.get("goals", {}).get("a"), int)],
        "match_stats": ms,
        "_understat": {
            "match_id": match_id,
            "shots": (matched or {}).get("shots"),
            "rosters": (matched or {}).get("rosters"),
            "info": info,
        },
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) >= 4:
        # py -m laliga.understat "Girona" "Barcelona" 2025-26 [YYYY-MM-DD]
        h, a, season = sys.argv[1], sys.argv[2], sys.argv[3]
        d = sys.argv[4] if len(sys.argv) > 4 else None
        res = understat_fetch_match_details(h, a, d, season)
    elif len(sys.argv) == 2:
        res = fetch_understat_match(sys.argv[1])
    else:
        print("usage: py -m laliga.understat <home> <away> <season> [date]  |  <match_id>")
        raise SystemExit(2)
    print(json.dumps(res, ensure_ascii=False, indent=2)[:2000] if res else "None")
