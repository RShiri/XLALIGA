"""
Understat source for the Premier League — xG / shots / PPDA / deep completions + shot-level xG.

Understat publishes rich, free xG data for the top-5 leagues (incl. the EPL) as JSON
blobs embedded in each page (``var X = JSON.parse('...')``). It used to be scrapeable
with plain ``urllib``, but the site now bot-blocks raw HTTP (returns a ~18 KB shell with
no data blobs), so we load pages through Selenium — the same browser the WhoScored scrape
already drives. The scraper passes its existing driver in (efficient, and serialised by
the run-lock); if none is given we spin up our own.

What we return (``understat_fetch_match_details``) is a **FotMob-shaped** dict so the
scraper's existing ``_parse_fotmob_*`` consumers can merge it alongside FotMob/WhoScored,
plus a ``_understat`` block carrying shot-level data (per-shot xG + coords + player) and
per-player roster stats (goals/assists/xG/xA/minutes) for the dashboard.

Blobs on a match page:
  · match_info  — h_xg/a_xg, h_shot/a_shot, h_shotOnTarget/a_shotOnTarget, h_deep/a_deep,
                  h_ppda/a_ppda, team_h/team_a, h/a (team ids), date
  · shotsData   — {h:[...], a:[...]}, each shot: minute, xG, player, result, X, Y, situation, ...
  · rostersData — {h:{...}, a:{...}}, each player: player, goals, assists, xG, xA, time, position

Season note: Understat keys a season by its START year — 2025 = 2025/26, 2026 = 2026/27.
"""
from __future__ import annotations

import re
import json
import time
import logging
import unicodedata
from datetime import datetime

log = logging.getLogger("epl.understat")

UNDERSTAT = "https://understat.com"
LEAGUE_SLUG = "EPL"


# ── team-name matching across feeds ───────────────────────────────────────────
def _key(name: str) -> str:
    """Accent-fold + strip club-agnostic suffix noise so 'AFC Bournemouth' == 'Bournemouth'.
    Keeps discriminating tokens ('united'/'city') so the two Manchester clubs stay distinct."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    for junk in ("afc ", " afc", " fc", "f.c.", "association football club"):
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


def _get_page(url: str, driver=None, wait: float = 4.0) -> str:
    own = driver is None
    d = driver or _new_driver()
    try:
        d.get(url)
        time.sleep(wait)
        return d.page_source
    finally:
        if own:
            try:
                d.quit()
            except Exception:
                pass


# ── blob decoding ─────────────────────────────────────────────────────────────
def _decode_blob(raw: str):
    """Decode an Understat ``JSON.parse('...')`` payload (hex-escaped, UTF-8)."""
    for attempt in (
        lambda r: json.loads(r.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")),
        lambda r: json.loads(r.encode("utf-8").decode("unicode_escape")),
    ):
        try:
            return attempt(raw)
        except Exception:
            continue
    return None


def _blobs(page: str) -> dict:
    out = {}
    for name, raw in re.findall(r"var\s+(\w+)\s*=\s*JSON\.parse\('(.+?)'\);", page, re.DOTALL):
        data = _decode_blob(raw)
        if data is not None:
            out[name] = data
    return out


# ── match discovery ───────────────────────────────────────────────────────────
def find_understat_match_id(home: str, away: str, date: str | None,
                            season: str, driver=None) -> str | None:
    """Find an Understat match id for home/away (+ optional YYYY-MM-DD) in a season.
    ``season`` is '2025-26' style; Understat uses the start year (2025)."""
    start_year = season.split("-")[0]
    page = _get_page(f"{UNDERSTAT}/league/{LEAGUE_SLUG}/{start_year}", driver)
    dates = _blobs(page).get("datesData")
    if not dates:
        log.warning("Understat: no datesData for %s %s", LEAGUE_SLUG, start_year)
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
    """Return the raw parsed blobs {match_info, shotsData, rostersData} for a match id."""
    page = _get_page(f"{UNDERSTAT}/match/{match_id}", driver)
    b = _blobs(page)
    info = b.get("match_info")
    if not info or "h_xg" not in info:
        # match_info sometimes appears as a different var; scan for the xG-bearing dict.
        for v in b.values():
            if isinstance(v, dict) and "h_xg" in v:
                info = v
                break
    if not info:
        log.warning("Understat: match_info not found for %s", match_id)
        return None
    return {"match_info": info, "shotsData": b.get("shotsData"), "rostersData": b.get("rostersData")}


def understat_fetch_match_details(home: str, away: str, date: str | None,
                                  season: str, driver=None, match_id: str | None = None) -> dict | None:
    """FotMob-shaped stats dict (so the scraper's parsers/merge consume it) + a `_understat`
    block with shot-level + roster data. Returns None if the match can't be found/parsed."""
    if match_id is None:
        match_id = find_understat_match_id(home, away, date, season, driver)
    if not match_id:
        return None
    parsed = fetch_understat_match(match_id, driver)
    if not parsed:
        return None
    info = parsed["match_info"]

    # Map Understat match_info → the same match_stats keys the scraper/dashboard use.
    ms = {
        "xg_home": _num(info.get("h_xg")), "xg_away": _num(info.get("a_xg")),
        "shots_home": _num(info.get("h_shot"), int), "shots_away": _num(info.get("a_shot"), int),
        "shots_on_target_home": _num(info.get("h_shotOnTarget"), int),
        "shots_on_target_away": _num(info.get("a_shotOnTarget"), int),
        "deep_home": _num(info.get("h_deep"), int), "deep_away": _num(info.get("a_deep"), int),
        "ppda_home": _num(info.get("h_ppda")), "ppda_away": _num(info.get("a_ppda")),
    }
    return {
        "_source": "understat",
        "match_id": match_id,
        "home_name": info.get("team_h", home),
        "away_name": info.get("team_a", away),
        "score": [_num(info.get("h_goals"), int), _num(info.get("a_goals"), int)],
        "match_stats": ms,
        "_understat": {
            "match_id": match_id,
            "shots": parsed.get("shotsData"),
            "rosters": parsed.get("rostersData"),
            "info": info,
        },
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) >= 4:
        # py -m epl.understat "Arsenal" "Chelsea" 2025-26 [YYYY-MM-DD]
        h, a, season = sys.argv[1], sys.argv[2], sys.argv[3]
        d = sys.argv[4] if len(sys.argv) > 4 else None
        res = understat_fetch_match_details(h, a, d, season)
    elif len(sys.argv) == 2:
        res = fetch_understat_match(sys.argv[1])
    else:
        print("usage: py -m epl.understat <home> <away> <season> [date]  |  <match_id>")
        raise SystemExit(2)
    print(json.dumps(res, ensure_ascii=False, indent=2)[:2000] if res else "None")
