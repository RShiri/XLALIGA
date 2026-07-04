"""
Premier League – Match Data Scraper

Two-source pipeline:
  1. FotMob   (cloudscraper, no browser) – polls for finished EPL matches,
               extracts stats, shots, lineups, xG, venue metadata.
  2. WhoScored (Selenium)               – full event stream (passes, shots,
               dribbles, etc.) in the exact format the renderer expects.

Output: epl/matches/YYYY_MM_DD_TeamA_vs_TeamB.json

Usage:
  # Watch continuously (checks every 5 min for newly finished matches):
  python -m epl.scraper

  # Fetch one specific FotMob match ID immediately:
  python -m epl.scraper --fotmob-id 4321567

  # Skip WhoScored (FotMob data only – limited pass networks):
  python -m epl.scraper --fotmob-only
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

log = logging.getLogger("epl.scraper")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCRAPER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

MATCHES_DIR  = Path(__file__).parent / "matches"
MATCHES_DIR.mkdir(exist_ok=True)

POLL_INTERVAL = int(os.environ.get("EPL_SCRAPE_POLL_SECONDS", 300))  # 5 min

# FotMob Premier League league id (name "Premier League" in the XML feed; the second
# tier is the EFL Championship id 48, which the id/name filter excludes).
EPL_FOTMOB_ID = int(os.environ.get("EPL_FOTMOB_LEAGUE_ID", 47))
EPL_FOTMOB_NAMES = {"premier league", "premierleague", "epl"}

# WhoScored Premier League fixtures page. WhoScored serves the CURRENT season from the
# season-less tournament URL (Region 252 = England, Tournament 2 = Premier League),
# which is what we want for a live season. For an archived season set EPL_WHOSCORED_URLS
# (pipe-separated) to the season-specific .../Seasons/<sid>/Stages/<stid>/Fixtures/...
# page. EPL_WHOSCORED_URL still works for a single page.
_WS_DEFAULT_URLS = "https://www.whoscored.com/Regions/252/Tournaments/2/England-Premier-League"
EPL_WS_BASES = [
    u.strip() for u in os.environ.get(
        "EPL_WHOSCORED_URLS",
        os.environ.get("EPL_WHOSCORED_URL", _WS_DEFAULT_URLS),
    ).split("|") if u.strip()
]
EPL_WS_BASE = EPL_WS_BASES[0]  # backward-compat alias

_fetched_ids: set[int] = set()  # avoid re-fetching in the same run

# EPL clubs keep stable names across FotMob/WhoScored/Understat, so no
# placeholder-name remapping is needed. Kept as an empty hook in case a feed ever
# uses an odd alias.
FOTMOB_NAME_OVERRIDES: dict[str, str] = {}

# ══════════════════════════════════════════════════════════════════════════
# SCHEDULE HELPERS – team name resolution without FotMob
# ══════════════════════════════════════════════════════════════════════════

_SCHEDULE_DIR   = Path(__file__).parent / "schedules"
_schedule_cache: list[dict] | None = None


def _load_schedule() -> list[dict]:
    """All fixtures across every season schedule (schedules/SCHEDULE_*.json)."""
    global _schedule_cache
    if _schedule_cache is None:
        out: list[dict] = []
        for f in sorted(_SCHEDULE_DIR.glob("SCHEDULE_*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.extend(data.get("matches", []))
            except Exception as exc:
                log.warning("Could not load schedule %s: %s", f.name, exc)
        _schedule_cache = out
    return _schedule_cache


def schedule_team_names(fotmob_id: int) -> tuple[str, str, str]:
    """Return (home, away, YYYY-MM-DD) from the season schedules for a FotMob id."""
    for m in _load_schedule():
        if m.get("fotmob_id") == fotmob_id:
            return m.get("home", ""), m.get("away", ""), (m.get("date") or "")[:10]
    return "", "", ""


def schedule_lookup_by_teams(home: str, away: str) -> tuple[int | None, str]:
    """Return (fotmob_id, YYYY-MM-DD) by fuzzy-matching team names in the schedule."""
    h_low, a_low = home.strip().lower(), away.strip().lower()
    for m in _load_schedule():
        sched_h = m.get("home", "").lower()
        sched_a = m.get("away", "").lower()
        if (h_low in sched_h or sched_h in h_low) and \
           (a_low in sched_a or sched_a in a_low):
            return m["fotmob_id"], (m.get("date") or "")[:10]
    return None, ""


# ══════════════════════════════════════════════════════════════════════════
# FOTMOB – no browser required
# ══════════════════════════════════════════════════════════════════════════

def _fotmob_scraper():
    try:
        import cloudscraper
        return cloudscraper.create_scraper()
    except ImportError:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )})
        return s


_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _http_get(url: str, headers: dict | None = None, timeout: int = 25):
    """GET a URL with a real-browser TLS fingerprint.

    FotMob (and many football data hosts) sit behind a Varnish/WAF edge that
    fingerprints the TLS/JA3 handshake and 403/404s the stock ``requests``/
    ``cloudscraper`` clients. ``curl_cffi`` impersonates Chrome's TLS so the
    request looks like a genuine browser -- this is what makes the FotMob JSON
    API reachable without a signed token. If curl_cffi isn't installed we fall
    back to the cloudscraper session (works once the request URL is correct).
    Returns a response object exposing ``.status_code``/``.text``/``.json()``.
    """
    hdr = {"User-Agent": _BROWSER_UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        hdr.update(headers)
    try:
        from curl_cffi import requests as _creq
        return _creq.get(url, headers=hdr, timeout=timeout, impersonate="chrome")
    except ImportError:
        return _fotmob_scraper().get(url, headers=hdr, timeout=timeout)


def fotmob_fetch_wc_matches(dates: "list[str] | None" = None) -> list[dict]:
    """
    Return WC 2026 matches from FotMob's XML feed (api.fotmob.com/matches?date=YYYYMMDD).
    FotMob's JSON leagues endpoint is defunct; this XML feed still works.

    ``dates`` (YYYYMMDD strings) overrides the default scan window. When omitted it
    scans yesterday + today (the live-watch use). Passing explicit dates lets the
    real-id lookup search the *actual* match date even when the catch-up sweep runs
    days later — otherwise a match outside the 2-day window is invisible and its id
    can never be recovered.
    """
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone, timedelta

    scraper = _fotmob_scraper()
    now_utc = datetime.now(timezone.utc)
    if dates:
        # de-dup while preserving order
        dates_to_check = list(dict.fromkeys(dates))
    else:
        dates_to_check = [
            (now_utc - timedelta(days=1)).strftime("%Y%m%d"),
            now_utc.strftime("%Y%m%d"),
        ]

    matches: list[dict] = []
    seen_ids: set = set()

    for date_str in dates_to_check:
        url = f"https://api.fotmob.com/matches?date={date_str}"
        log.info("FotMob XML: fetching matches for %s …", date_str)
        try:
            resp = scraper.get(url, timeout=20)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except Exception as exc:
            log.error("FotMob XML fetch failed (%s): %s", date_str, exc)
            continue

        for league in root.iter("league"):
            league_name = league.get("name", "")
            # Filter to the Premier League: the English top flight is name "Premier
            # League" id 47 in this feed (the second tier is the Championship id 48).
            if str(league.get("id", "")) != str(EPL_FOTMOB_ID) \
               and league_name.strip().lower() not in EPL_FOTMOB_NAMES:
                continue
            for match in league.iter("match"):
                mid = match.get("id")
                if not mid or mid in seen_ids:
                    continue
                seen_ids.add(mid)
                status_code = match.get("Status", "N")
                h_score = match.get("hScore", "0")
                a_score = match.get("aScore", "0")
                time_str = match.get("time", "")
                # Parse match UTC time from FotMob XML format "DD.MM.YYYY HH:MM"
                utc_time = None
                try:
                    utc_time = datetime.strptime(time_str, "%d.%m.%Y %H:%M").replace(
                        tzinfo=timezone.utc
                    )
                except Exception:
                    pass
                # Consider finished if: status is 'FT'/'AET'/'PEN', OR
                # score differs from 0-0, OR kick-off was >115 min ago
                is_finished = (
                    status_code in ("FT", "AET", "PEN", "FT_PEN")
                    or (h_score != "0" or a_score != "0")
                    or (utc_time is not None and (now_utc - utc_time).total_seconds() > 115 * 60)
                )
                matches.append({
                    "id":       int(mid),
                    "home":     {"name": match.get("hTeam", ""), "id": match.get("hId")},
                    "away":     {"name": match.get("aTeam", ""), "id": match.get("aId")},
                    "status":   {
                        "scoreStr": f"{h_score} - {a_score}",
                        "finished": is_finished,
                        "utcTime":  utc_time.isoformat() if utc_time else "",
                    },
                    "_league":  league_name,
                })

    log.info("FotMob XML: found %d EPL matches across checked dates", len(matches))
    return matches


def _fotmob_unavailable_stub() -> dict:
    return {"_fotmob_unavailable": True, "general": {}, "header": {}, "content": {}}


def _fotmob_xmas_token(path: str) -> str:
    """Resolve FotMob's required ``x-mas`` request header, or '' if not configured.

    FotMob gates ``www.fotmob.com/api/*`` behind a signed ``x-mas`` header whose secret
    they rotate, so it can't be hard-coded reliably. We support, in order:
      1. FOTMOB_XMAS_TOKEN env — a literal x-mas value (copy it from your browser's
         DevTools → Network → any matchDetails request → Request Headers).
      2. FOTMOB_TOKEN_URL env — a helper endpoint that returns the token (raw text or
         JSON with an ``x-mas``/``token`` field), given the api ``path`` as ?url=.
    Returns '' when neither is set, in which case we still try the request bare
    (cloudscraper clears Cloudflare and FotMob's edge sometimes serves it tokenless)."""
    tok = os.environ.get("FOTMOB_XMAS_TOKEN", "").strip()
    if tok:
        return tok
    token_url = os.environ.get("FOTMOB_TOKEN_URL", "").strip()
    if token_url:
        try:
            import urllib.parse
            sep = "&" if "?" in token_url else "?"
            resp = _fotmob_scraper().get(
                f"{token_url}{sep}url={urllib.parse.quote(path, safe='')}", timeout=15)
            if resp.ok:
                body = resp.text.strip()
                try:
                    j = resp.json()
                    return str(j.get("x-mas") or j.get("token") or body)
                except Exception:
                    return body
        except Exception as exc:
            log.warning("FotMob token helper failed: %s", exc)
    return ""


def fotmob_fetch_match_details(match_id: int) -> dict:
    """Fetch FotMob's JSON matchDetails for ``match_id``.

    Returns the parsed JSON (keys ``general``/``header``/``content``) on success, or a
    stub with ``_fotmob_unavailable=True`` on any failure so build_match_json() falls
    back to WhoScored only — i.e. a FotMob outage degrades, it never aborts the match.
    """
    # FotMob moved matchDetails behind /api/data/ -- the old /api/matchDetails path
    # now 404s for everyone (that silent 404 is why FotMob stats/xG vanished from the
    # pipeline). Hit the live path first, keep the legacy one as a fallback in case
    # FotMob flips it back. _http_get uses a browser TLS fingerprint (curl_cffi) so
    # the Varnish edge serves the JSON without any signed x-mas token.
    paths = [
        f"/api/data/matchDetails?matchId={match_id}",
        f"/api/matchDetails?matchId={match_id}",
    ]

    last = None
    for path in paths:
        url = f"https://www.fotmob.com{path}"
        headers = {"Referer": f"https://www.fotmob.com/match/{match_id}"}
        token = _fotmob_xmas_token(path)  # only if explicitly configured (no longer required)
        if token:
            headers["x-mas"] = token
        try:
            resp = _http_get(url, headers=headers, timeout=25)
            last = resp.status_code
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = None
                if data and (data.get("header") or data.get("content") or data.get("general")):
                    log.info("FotMob matchDetails: fetched id=%s (%s)", match_id, path)
                    data.setdefault("general", {})
                    data.setdefault("header", {})
                    data.setdefault("content", {})
                    return data
                log.warning("FotMob matchDetails id=%s (%s): 200 but empty/unexpected body.",
                            match_id, path)
            else:
                log.warning("FotMob matchDetails id=%s (%s): HTTP %s.",
                            match_id, path, resp.status_code)
        except Exception as exc:
            log.warning("FotMob matchDetails id=%s (%s) failed: %s", match_id, path, exc)

    log.warning(
        "FotMob matchDetails unavailable for id=%s (last HTTP %s). Building from "
        "WhoScored only. (If this persists FotMob may have changed the API path again.)",
        match_id, last,
    )
    return _fotmob_unavailable_stub()


# ══════════════════════════════════════════════════════════════════════════
# SOFASCORE – no browser, no signed token (fallback stats source)
# ══════════════════════════════════════════════════════════════════════════

# SofaScore stat name (lowercased) → FotMob match-stats title, so the SofaScore
# payload can be shaped like FotMob's and reuse _parse_fotmob_stats() verbatim.
_SS_STAT_TITLES = {
    "expected goals":   "Expected goals (xG)",
    "ball possession":  "Ball possession",
    "total shots":      "Total shots",
    "shots on target":  "Shots on target",
    "big chances":      "Big chances",
    "goalkeeper saves": "Saves",
    "saves":            "Saves",
    "fouls":            "Fouls",
    "passes":           "Passes",
    "accurate passes":  "Accurate passes",
}
# SofaScore single-letter positions → FotMob-ish codes _parse_fotmob_lineup understands.
_SS_POS = {"G": "GK", "D": "CB", "M": "CM", "F": "ST"}


def _ss_num(item, side):
    """Numeric value of a SofaScore statisticsItem for 'home'/'away'."""
    v = item.get(side + "Value")
    if isinstance(v, (int, float)):
        return v
    raw = str(item.get(side, "")).strip()
    m = re.match(r"-?\d+(?:\.\d+)?", raw.replace("%", ""))
    return float(m.group()) if m else None


def sofascore_fetch_match_details(home: str, away: str, around_date: str | None) -> dict:
    """Best-effort FotMob-shaped matchDetails built from SofaScore's open JSON API.

    SofaScore needs no signed token (just a browser UA), so it's the most reliable
    second source when FotMob's x-mas gate blocks us. Returns a dict shaped like
    FotMob's (so _parse_fotmob_stats/_lineup consume it unchanged) tagged
    ``_source_name='sofascore'``, or the unavailable stub on any failure. Provides
    stats + lineups + score/venue; shot events stay with WhoScored (whose event
    stream is richer and whose pitch orientation the renderer already handles)."""
    from datetime import date, timedelta
    try:
        from epl.knockout_resolve import _team_key  # alias-aware name compare
    except Exception:
        def _team_key(n): return re.sub(r"[^a-z]", "", (n or "").lower())

    scraper = _fotmob_scraper()
    hdr = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
           "Accept": "application/json"}

    def _get(url):
        r = scraper.get(url, headers=hdr, timeout=20)
        return r.json() if r.ok else None

    # 1. find the event id by scanning the scheduled-events feed around the date
    want = {_team_key(home), _team_key(away)}
    days = []
    if around_date:
        try:
            d0 = date.fromisoformat(around_date[:10])
            days = [(d0 + timedelta(days=o)).isoformat() for o in (0, -1, 1)]
        except Exception:
            days = []
    if not days:
        days = [datetime.now(timezone.utc).date().isoformat()]

    event = None
    try:
        for day in days:
            data = _get(f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{day}")
            for ev in (data or {}).get("events", []):
                h = ev.get("homeTeam", {}).get("name", "")
                a = ev.get("awayTeam", {}).get("name", "")
                if {_team_key(h), _team_key(a)} == want:
                    event = ev
                    break
            if event:
                break
    except Exception as exc:
        log.warning("SofaScore schedule lookup failed: %s", exc)

    if not event:
        log.info("SofaScore: no event found for %s vs %s near %s", home, away, around_date)
        return _fotmob_unavailable_stub()

    eid = event.get("id")
    swap = _team_key(event.get("homeTeam", {}).get("name", "")) != _team_key(home)  # SS home != our home
    log.info("SofaScore: matched event id=%s for %s vs %s", eid, home, away)

    # 2. statistics → FotMob-titled stats list
    stat_items = []
    try:
        sdata = _get(f"https://api.sofascore.com/api/v1/event/{eid}/statistics") or {}
        groups = next((p.get("groups", []) for p in sdata.get("statistics", [])
                       if p.get("period") == "ALL"), [])
        for g in groups:
            for it in g.get("statisticsItems", []):
                title = _SS_STAT_TITLES.get(str(it.get("name", "")).strip().lower())
                if not title:
                    continue
                hv, av = _ss_num(it, "home"), _ss_num(it, "away")
                if hv is None or av is None:
                    continue
                if swap:
                    hv, av = av, hv
                stat_items.append({"title": title, "stats": [hv, av]})
    except Exception as exc:
        log.warning("SofaScore statistics parse failed: %s", exc)

    # 3. lineups → FotMob-shaped players
    def _ss_players(side_obj):
        out = []
        for p in (side_obj or {}).get("players", []):
            pl = p.get("player", {})
            out.append({
                "id":          pl.get("id"),
                "name":        pl.get("name", ""),
                "shirt":       p.get("jerseyNumber") or pl.get("jerseyNumber") or 0,
                "position":    _SS_POS.get((p.get("position") or pl.get("position") or "")[:1], "CM"),
                "positionRow": 99 if p.get("substitute") else 0,
            })
        return out

    home_lineup = away_lineup = []
    try:
        ld = _get(f"https://api.sofascore.com/api/v1/event/{eid}/lineups") or {}
        ss_home, ss_away = ld.get("home"), ld.get("away")
        if swap:
            ss_home, ss_away = ss_away, ss_home
        home_lineup, away_lineup = _ss_players(ss_home), _ss_players(ss_away)
    except Exception as exc:
        log.warning("SofaScore lineups parse failed: %s", exc)

    # 4. score / teams / venue
    hs = event.get("homeScore", {}).get("current", 0)
    as_ = event.get("awayScore", {}).get("current", 0)
    if swap:
        hs, as_ = as_, hs
    ts = event.get("startTimestamp")
    utc = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""
    venue = {}
    try:
        det = _get(f"https://api.sofascore.com/api/v1/event/{eid}") or {}
        ven = det.get("event", {}).get("venue", {}) or {}
        venue = {"venue": ven.get("stadium", {}).get("name", ""),
                 "venueCity": ven.get("city", {}).get("name", ""),
                 "venueCountry": ven.get("country", {}).get("name", "")}
    except Exception:
        pass

    stage = (event.get("roundInfo", {}).get("name")
             or event.get("tournament", {}).get("name", "Group Stage"))

    if not stat_items and not home_lineup and not away_lineup:
        log.warning("SofaScore: event %s found but no stats/lineups extracted.", eid)
        return _fotmob_unavailable_stub()

    log.info("SofaScore: built details (%d stats, %d+%d players)",
             len(stat_items), len(home_lineup), len(away_lineup))
    return {
        "_source_name": "sofascore",
        "general": {"matchId": eid, "parentLeagueName": stage, **venue},
        "header":  {"teams": [{"name": home, "id": None}, {"name": away, "id": None}],
                    "status": {"scoreStr": f"{hs} - {as_}", "utcTime": utc, "finished": True}},
        "content": {"stats":  {"Periods": {"All": {"stats": stat_items}}},
                    "lineup": {"home": {"players": home_lineup},
                               "away": {"players": away_lineup}},
                    "shotmap": {"shots": []}},
    }


def _ev_is_shootout(e: dict) -> bool:
    """True for penalty-shootout events (WhoScored period 5 / "PenaltyShootout").

    Mirrors epl_dashboard/xg_model.is_shootout (scraper can't import the dashboard
    module). Shootout kicks decide a drawn knockout tie but are not match shots/goals —
    keep them out of stats, xG, shot maps and the goals timeline; report them as a
    separate penalty score. Extra-time events (periods 3/4) are real and stay."""
    p = e.get("period", {})
    if isinstance(p, dict):
        return p.get("value") == 5 or "Shoot" in (p.get("displayName") or "")
    return "Shoot" in str(p or "")


def _shootout_score(events: list, home_tid, away_tid) -> tuple:
    """(home_pens, away_pens) scored in the penalty shootout, or (None, None) if none."""
    pens = [e for e in events if _ev_is_shootout(e)]
    if not pens:
        return None, None
    def scored(tid):
        return sum(1 for e in pens
                   if e.get("teamId") == tid
                   and e.get("type", {}).get("displayName") == "Goal")
    return scored(home_tid), scored(away_tid)


def _compute_ws_stats(events: list, home_tid, away_tid) -> dict:
    """Compute match stats from the WhoScored event stream.

    Used as the stats source when FotMob's matchDetails API is unavailable.
    xG is intentionally absent — WhoScored events carry no expected-goals data.
    """
    SHOT_ALL = {"Goal", "SavedShot", "MissedShots", "ShotOnPost", "BlockedShot"}
    SHOT_ON  = {"Goal", "SavedShot"}
    DUEL     = {"Aerial", "Tackle", "TakeOn"}

    # Drop penalty-shootout kicks (period 5): they decide the tie but are not match
    # shots — counting them balloons "shots"/"on target"/"big chances" (and xG).
    events = [e for e in events if not _ev_is_shootout(e)]

    def _t(e): return e.get("type", {}).get("displayName", "")
    def _o(e): return e.get("outcomeType", {}).get("displayName", "")

    def per_team(fn):
        return {"home": fn(home_tid), "away": fn(away_tid)}

    shots = per_team(lambda tid: sum(1 for e in events if _t(e) in SHOT_ALL and e.get("teamId") == tid))
    sot   = per_team(lambda tid: sum(1 for e in events if _t(e) in SHOT_ON  and e.get("teamId") == tid))
    # Save events belong to the goalkeeper's (defending) team.
    saves = per_team(lambda tid: sum(1 for e in events if _t(e) == "Save" and e.get("teamId") == tid))
    # A Foul event is logged twice — Successful for the team that won it,
    # Unsuccessful for the team that committed it. "Fouls" = fouls committed.
    fouls = per_team(lambda tid: sum(1 for e in events
                                     if _t(e) == "Foul" and _o(e) == "Unsuccessful"
                                     and e.get("teamId") == tid))
    bc    = per_team(lambda tid: sum(1 for e in events if e.get("teamId") == tid
                                     and any(q.get("type", {}).get("displayName") == "BigChance"
                                             for q in e.get("qualifiers", []))))

    # Duels won = won aerials/tackles/take-ons. Every duel has one winner, so the
    # percentage is each team's share of all won duels.
    dw = per_team(lambda tid: sum(1 for e in events if _t(e) in DUEL
                                  and _o(e) == "Successful" and e.get("teamId") == tid))
    dw_total = dw["home"] + dw["away"]
    duels_won = {
        "home": int(round(100 * dw["home"] / dw_total)) if dw_total else 0,
        "away": int(round(100 * dw["away"] / dw_total)) if dw_total else 0,
    }

    # Passes (total / accurate / accuracy%).
    pt = per_team(lambda tid: sum(1 for e in events if _t(e) == "Pass" and e.get("teamId") == tid))
    pa = per_team(lambda tid: sum(1 for e in events if _t(e) == "Pass"
                                  and _o(e) == "Successful" and e.get("teamId") == tid))
    passes_accuracy = {
        "home": int(round(100 * pa["home"] / pt["home"])) if pt["home"] else 0,
        "away": int(round(100 * pa["away"] / pt["away"])) if pt["away"] else 0,
    }

    # Possession proxy: share of total passes (touch-time data is not available).
    pt_total = pt["home"] + pt["away"]
    possession = {
        "home": int(round(100 * pt["home"] / pt_total)) if pt_total else 50,
        "away": int(round(100 * pt["away"] / pt_total)) if pt_total else 50,
    }

    return {
        "shots":               shots,
        "shots_on_target":     sot,
        "saves":               saves,
        "fouls":               fouls,
        "big_chances_created": bc,
        "duels_won":           duels_won,
        "possession":          possession,
        "passes_total":        pt,
        "passes_accurate":     pa,
        "passes_accuracy":     passes_accuracy,
    }


# FotMob stat row "key" → our canonical stat name. FotMob's "key" is stable across
# title/locale changes (the old title-based map broke when FotMob restructured stats
# into groups). Values are normalised to the same units WhoScored produces so the two
# sources can be averaged (see _compute_ws_stats / _average_sources).
_FM_STAT_KEYS = {
    "BallPossesion":           "possession",            # %
    "expected_goals":          "xg",                    # float
    "total_shots":             "shots",                 # count
    "ShotsOnTarget":           "shots_on_target",       # count
    "big_chance":              "big_chances_created",   # count
    "big_chance_missed_title": "big_chances_missed",    # count
    "keeper_saves":            "saves",                 # count
    "fouls":                   "fouls",                 # count
    "passes":                  "passes_total",          # count
    "corners":                 "corners",               # count (extra)
    "interceptions":           "interceptions",         # count (extra)
    "shot_blocks":             "blocks",                # count (extra)
    "clearances":              "clearances",            # count (extra)
    "Offsides":                "offsides",              # count (extra)
}

# Fallback map keyed by the human title, for payloads that lack the stable "key"
# field (FotMob's older flat layout, and the SofaScore-shaped fallback that reuses
# this parser). Keeps the parser working if FotMob changes its structure again.
_FM_TITLE_KEYS = {
    "Expected goals (xG)": "xg",
    "Ball possession":     "possession",
    "Total shots":         "shots",
    "Shots on target":     "shots_on_target",
    "Big chances":         "big_chances_created",
    "Big chances missed":  "big_chances_missed",
    "Saves":               "saves",
    "Fouls":               "fouls",
    "Passes":              "passes_total",
    "Accurate passes":     "passes_accurate",
    "Corners":             "corners",
}


def _fm_num(v):
    """Leading number out of a FotMob stat cell ('1.04', 46, '450 (90%)' → 1.04/46/450)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    m = re.match(r"-?\d+(?:\.\d+)?", str(v).strip())
    if not m:
        return None
    x = m.group()
    return float(x) if "." in x else int(x)


def _fm_pct(v):
    """Percentage inside a FotMob cell like '450 (90%)' → 90."""
    m = re.search(r"\((\d+(?:\.\d+)?)\s*%\)", str(v))
    return _fm_num(m.group(1)) if m else None


def _parse_fotmob_stats(fm_data: dict) -> dict:
    """Extract a canonical {stat: {home, away}} dict from FotMob matchDetails.

    Handles FotMob's current *grouped* layout (Periods.All.stats = list of groups,
    each with an inner ``stats`` list of rows) and the older flat layout. Returns
    values in the same units WhoScored uses so the two sources can be averaged.
    """
    stats: dict = {}
    try:
        all_stats = (
            fm_data.get("content", {})
            .get("stats", {})
            .get("Periods", {})
            .get("All", {})
            .get("stats", [])
        )
        # Flatten: each element is either a group ({title,key,stats:[rows]}) or, in the
        # legacy layout, a row itself. A group's inner items carry their own "stats".
        rows = []
        for el in all_stats:
            inner = el.get("stats") if isinstance(el, dict) else None
            if isinstance(inner, list) and inner and isinstance(inner[0], dict) and "stats" in inner[0]:
                rows.extend(inner)        # grouped layout
            elif isinstance(el, dict):
                rows.append(el)           # flat layout

        for row in rows:
            vals = row.get("stats")
            if not isinstance(vals, list) or len(vals) < 2:
                continue
            h_raw, a_raw = vals[0], vals[1]
            if h_raw is None and a_raw is None:
                continue

            # Accurate passes carry both a count and a % — split into two canonical stats.
            if row.get("key") == "accurate_passes":
                stats["passes_accurate"] = {"home": _fm_num(h_raw), "away": _fm_num(a_raw)}
                hp, ap = _fm_pct(h_raw), _fm_pct(a_raw)
                if hp is not None or ap is not None:
                    stats["passes_accuracy"] = {"home": hp, "away": ap}
                continue
            # Duels won: FotMob gives a raw COUNT; WhoScored gives a % share. Convert to
            # a share so the two are averaged on the same scale (and match the site).
            if row.get("key") == "duel_won":
                hn, an = _fm_num(h_raw), _fm_num(a_raw)
                tot = (hn or 0) + (an or 0)
                if tot:
                    stats["duels_won"] = {"home": int(round(100 * (hn or 0) / tot)),
                                          "away": int(round(100 * (an or 0) / tot))}
                continue

            canon = (_FM_STAT_KEYS.get(row.get("key", ""))
                     or _FM_TITLE_KEYS.get(row.get("title", "")))
            if not canon or canon in stats:
                continue  # first non-empty row for a key wins (groups repeat keys)
            stats[canon] = {"home": _fm_num(h_raw), "away": _fm_num(a_raw)}
    except Exception as exc:
        log.warning("FotMob stats parse error: %s", exc)

    # Derive passes_accuracy from totals if FotMob didn't give the % directly.
    pt, pa = stats.get("passes_total", {}), stats.get("passes_accurate", {})
    if pt and pa and "passes_accuracy" not in stats:
        for side in ("home", "away"):
            if pt.get(side) and pa.get(side):
                stats.setdefault("passes_accuracy", {})[side] = int(round(pa[side] / pt[side] * 100))

    return stats


def _parse_fotmob_shots(fm_data: dict, home_id: int, away_id: int) -> list[dict]:
    """
    Convert FotMob shotmap shots into WhoScored-compatible event dicts.
    These are appended to the events list so the renderer can use them.
    """
    shots_raw = (
        fm_data.get("content", {})
        .get("shotmap", {})
        .get("shots", [])
    ) or []

    events = []
    for s in shots_raw:
        tid = s.get("teamId")
        # FotMob x/y are 0-100 from attacking perspective; flip away team
        x = float(s.get("x", 50))
        y = float(s.get("y", 50))
        if tid == away_id:
            x = 100 - x
            y = 100 - y

        outcome_map = {
            "Goal":          "Goal",
            "SavedShot":     "SavedShot",
            "AttemptSaved":  "SavedShot",
            "Miss":          "MissedShots",
            "BlockedShot":   "MissedShots",
            "ShotOnPost":    "ShotOnPost",
        }
        ev_type = outcome_map.get(s.get("eventType", ""), "MissedShots")
        is_goal = ev_type == "Goal"

        quals = []
        if s.get("isOnTarget"):
            pass
        if s.get("isBigChance"):
            quals.append({"type": {"displayName": "BigChance"}})
        bp = s.get("situation", "")
        if "Penalty" in bp:
            quals.append({"type": {"displayName": "Penalty"}})
        foot = s.get("shotType", "")
        if foot:
            quals.append({"type": {"displayName": "RightFoot" if "right" in foot.lower() else "LeftFoot"}})

        events.append({
            "id":           float(s.get("id", 0)),
            "eventId":      s.get("id", 0),
            "minute":       s.get("min", 0),
            "second":       0,
            "teamId":       tid,
            "x":            x,
            "y":            y,
            "expandedMinute": s.get("min", 0),
            "period":       {"displayName": "FirstHalf" if s.get("min", 0) <= 45 else "SecondHalf",
                             "value": 1 if s.get("min", 0) <= 45 else 2},
            "type":         {"displayName": ev_type, "value": 16 if is_goal else 13},
            "outcomeType":  {"displayName": "Successful" if is_goal else "Unsuccessful",
                             "value": 1 if is_goal else 0},
            "qualifiers":   quals,
            "satisfiedEventsTypes": [],
            "isTouch":      True,
            "playerId":     s.get("playerId"),
            "_source":      "fotmob",
        })

    return events


def _parse_fotmob_lineup(fm_data: dict, side: str) -> list[dict]:
    """Extract player list from FotMob lineup (home or away).

    FotMob's current shape is content.lineup.homeTeam/awayTeam with separate
    ``starters`` and ``subs`` lists (each player carries ``shirtNumber``). This is a
    fallback only — WhoScored supplies the rich lineups (positions, ratings), so we
    just need names/shirts here for FotMob-only matches.
    """
    lineup = fm_data.get("content", {}).get("lineup", {})
    if not isinstance(lineup, dict):
        return []
    team = lineup.get("homeTeam" if side == "home" else "awayTeam")
    if not isinstance(team, dict):
        # legacy shape content.lineup.home.players
        team = lineup.get(side, {})
        legacy = team.get("players", []) if isinstance(team, dict) else []
        starters, subs = legacy, []
    else:
        starters = team.get("starters", []) or []
        subs = team.get("subs", []) or []

    players = []
    for is_starter, group in ((True, starters), (False, subs)):
        for p in group:
            if not isinstance(p, dict):
                continue
            players.append({
                "playerId":     p.get("id"),
                "name":         p.get("name") or p.get("fullName", ""),
                "shirtNo":      p.get("shirtNumber", p.get("shirt", 0)),
                "position":     "MC",  # WhoScored overrides; FotMob gives only positionId
                "isFirstEleven": is_starter,
                "stats":        {},
            })
    return players


def _parse_fotmob_venue(fm_data: dict) -> dict:
    """Extract venue/city/stage from FotMob matchDetails.

    Venue/attendance now live under content.matchFacts.infoBox.Stadium; the stage
    name is the parent league / round. All best-effort with graceful fallbacks."""
    general = fm_data.get("general", {})
    info = (fm_data.get("content", {}).get("matchFacts", {}) or {}).get("infoBox", {}) or {}
    stadium = info.get("Stadium", {}) if isinstance(info.get("Stadium"), dict) else {}
    stage = (general.get("parentLeagueName")
             or general.get("leagueName")
             or "Group Stage")
    return {
        "venue":   stadium.get("name") or general.get("venue", ""),
        "city":    stadium.get("city") or general.get("venueCity", ""),
        "country": stadium.get("country") or general.get("venueCountry", "England"),
        "stage":   stage,
    }


# ══════════════════════════════════════════════════════════════════════════
# WHOSCORED – Selenium (full event stream)
# ══════════════════════════════════════════════════════════════════════════

def whoscored_fetch_match(ws_url: str, timeout: int = 30) -> dict | None:
    """
    Open a WhoScored match URL with Selenium, extract matchCentreData JSON.
    Returns the parsed dict or None on failure.
    """
    driver = None
    try:
        import undetected_chromedriver as uc
        options = uc.ChromeOptions()
        if os.environ.get("EPL_VISIBLE") != "1":
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        driver = uc.Chrome(options=options)
    except Exception as _uc_exc:
        # undetected-chromedriver breaks on Chrome version bumps (ImportError OR
        # SessionNotCreatedException); plain Selenium + Selenium Manager is the robust
        # fallback and clears WhoScored's Cloudflare fine in practice.
        log.info("undetected-chromedriver unavailable (%s); using plain selenium", _uc_exc)
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        options = Options()
        if os.environ.get("EPL_VISIBLE") != "1":
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
        driver = webdriver.Chrome(options=options)

    log.info("WhoScored: loading %s …", ws_url)
    try:
        driver.get(ws_url)
        time.sleep(timeout)

        html   = driver.page_source
        marker = "matchCentreData:"
        idx    = html.find(marker)
        if idx == -1:
            log.warning("WhoScored: matchCentreData not found in page source.")
            return None

        snippet = html[idx + len(marker):].strip()

        # Extract JSON by matching braces
        if "matchCentreEventTypeJson" in snippet:
            json_str = snippet.split("matchCentreEventTypeJson")[0].strip().rstrip(",")
        else:
            # Fallback: count braces
            depth, end = 0, 0
            for i, ch in enumerate(snippet):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            json_str = snippet[:end]

        data = json.loads(json_str)
        log.info("WhoScored: parsed %d events.", len(data.get("events", [])))
        return data

    except Exception as exc:
        log.error("WhoScored scrape error: %s", exc)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# WhoScored uses different names for some teams; map to the slug fragment they use.
_WS_NAME_ALIASES: dict[str, str] = {
    "south korea":            "republic-of-korea",
    "korea republic":         "republic-of-korea",
    "cape verde":             "cabo-verde",
    "ivory coast":            "ivory-coast",
    "cote d'ivoire":          "ivory-coast",
    "côte d'ivoire":          "ivory-coast",
    "dr congo":               "dr-congo",
    "democratic republic of congo": "dr-congo",
    "bosnia and herzegovina": "bosnia-and-herzegovina",
    "new zealand":            "new-zealand",
    "saudi arabia":           "saudi-arabia",
    "united states":          "usa",
    "turkiye":                "turkiye",
    "turkey":                 "turkiye",
}


def _ws_slug_key(name: str) -> str:
    """Normalise a team name to the slug fragment WhoScored would use."""
    lower = name.lower().strip()
    if lower in _WS_NAME_ALIASES:
        return _WS_NAME_ALIASES[lower]
    return re.sub(r"[^a-z0-9]+", "-", lower).strip("-")


def _ws_cache_lookup(home_name: str, away_name: str) -> int | None:
    """Check whoscored_ids.json for a pre-known match ID."""
    cache_path = Path(__file__).parent / "whoscored_ids.json"
    if not cache_path.exists():
        return None
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        h_key = _ws_slug_key(home_name)
        a_key = _ws_slug_key(away_name)
        for ws_id, entry in cache.items():
            slug = entry.get("slug", "")
            if h_key in slug and a_key in slug:
                log.info("WhoScored: cache hit — ID %s for %s vs %s", ws_id, home_name, away_name)
                return int(ws_id)
    except Exception as exc:
        log.warning("WhoScored: cache lookup error: %s", exc)
    return None


def _build_whoscored_url(home_name: str, away_name: str, ws_match_id: int | None) -> str | None:
    """Construct a WhoScored match URL. Uses cached slug when the ID is known."""
    if ws_match_id is None:
        return None
    # Prefer the cached slug so aliased names (Cape Verde → cabo-verde) work correctly
    cache_path = Path(__file__).parent / "whoscored_ids.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            entry = cache.get(str(ws_match_id))
            if entry and entry.get("slug"):
                slug = entry["slug"]
                return f"https://www.whoscored.com/matches/{ws_match_id}/live/england-premier-league-{slug}"
        except Exception:
            pass
    # Fallback: derive slug from team names
    h = _ws_slug_key(home_name)
    a = _ws_slug_key(away_name)
    return f"https://www.whoscored.com/matches/{ws_match_id}/live/england-premier-league-{h}-{a}"


def whoscored_search_match_id(home_name: str, away_name: str) -> int | None:
    """Return WhoScored match ID: cache first, then live competition page search."""
    cached = _ws_cache_lookup(home_name, away_name)
    if cached:
        return cached

    driver = None
    try:
        import undetected_chromedriver as uc
        options = uc.ChromeOptions()
        if os.environ.get("EPL_VISIBLE") != "1":
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = uc.Chrome(options=options)
    except Exception as _uc_exc:
        log.info("undetected-chromedriver unavailable (%s); using plain selenium", _uc_exc)
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        options = Options()
        if os.environ.get("EPL_VISIBLE") != "1":
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
        driver = webdriver.Chrome(options=options)

    log.info("WhoScored: searching for %s vs %s …", home_name, away_name)
    try:
        h_key = re.sub(r"[^a-z0-9]", "", home_name.lower())
        a_key = re.sub(r"[^a-z0-9]", "", away_name.lower())
        for base in EPL_WS_BASES:
            try:
                driver.get(base)
            except Exception as exc:
                log.warning("WhoScored: failed to load %s (%s)", base, exc)
                continue
            time.sleep(14)
            stage = re.search(r"/Stages/(\d+)/", base)
            log.info("WhoScored: scanning stage %s …", stage.group(1) if stage else base)
            for el in driver.find_elements("css selector", "a[href*='/matches/']"):
                href = el.get_attribute("href") or ""
                combined = re.sub(r"[^a-z0-9]", "", href.lower())
                if h_key in combined and a_key in combined:
                    m = re.search(r"/matches/(\d+)/", href)
                    if m:
                        mid = int(m.group(1))
                        log.info("WhoScored: found match ID %d", mid)
                        return mid
        log.warning("WhoScored: match ID not found for %s vs %s (scanned %d stage page(s))",
                    home_name, away_name, len(EPL_WS_BASES))
        return None
    except Exception as exc:
        log.error("WhoScored search error: %s", exc)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
# MULTI-SOURCE MERGE — average overlapping stats across providers
# ══════════════════════════════════════════════════════════════════════════

# Stats kept with decimals (xG); everything else is rounded to a whole number.
_FLOAT_STATS = {"xg"}

# Complementary percentages (home + away ~= 100): NOT event counts, so the "keep the
# larger" rule must not be applied per side (maxing both sides would push the sum past
# 100). Keep one source's coherent pair instead -- the one summing closest to 100.
_PAIR_STATS = {"possession", "duels_won"}


def _merge_sources(by_source: "dict[str, dict]") -> dict:
    """Combine each canonical stat across the sources that provide it by keeping the
    LARGER value (not the average).

    Rationale: when two providers disagree on a count it's almost always because the
    lesser one missed an event (a shot/pass/foul it didn't log), so the bigger number
    is the more complete one. ``by_source`` maps source name -> {stat:{home,away}}
    (already normalised to the same keys + units). Event-count stats take the per-side
    maximum; a stat only one source has (xG is FotMob-only, etc.) is used as-is.
    Complementary percentages (possession, duels) are kept as a coherent single-source
    pair so they still sum to ~100. The result is the nested {stat:{home,away}}
    ``match_stats`` the renderer and dashboard read."""
    keys: set = set()
    for s in by_source.values():
        keys.update(s.keys())

    merged: dict = {}
    for k in sorted(keys):
        pairs = [d[k] for d in by_source.values() if isinstance(d.get(k), dict)]
        if not pairs:
            continue
        if k in _PAIR_STATS:
            def _imbalance(p):
                h, a = p.get("home"), p.get("away")
                return abs(100 - ((h or 0) + (a or 0))) if h is not None and a is not None else 999
            best = min(pairs, key=_imbalance)
            for side in ("home", "away"):
                if best.get(side) is not None:
                    merged.setdefault(k, {})[side] = best[side]
        else:
            for side in ("home", "away"):
                vals = [p[side] for p in pairs if p.get(side) is not None]
                if not vals:
                    continue
                v = max(vals)
                merged.setdefault(k, {})[side] = round(v, 2) if k in _FLOAT_STATS else int(round(v))
    return merged


# ══════════════════════════════════════════════════════════════════════════
# BUILD EPL MATCH JSON
# ══════════════════════════════════════════════════════════════════════════

def build_match_json(fm_data: dict, ws_data: dict | None,
                     xml_match: dict | None = None) -> dict:
    """
    Merge FotMob details + optional WhoScored event stream into the
    epl match schema expected by renderer.py.

    When FotMob JSON details are unavailable (fm_data has _fotmob_unavailable=True),
    names/scores are taken from xml_match (the dict parsed from the XML feed)
    and ws_data is the mandatory event source.
    """
    fotmob_unavailable = fm_data.get("_fotmob_unavailable", False)

    # ── Team names & IDs ─────────────────────────────────────────────────────
    if fotmob_unavailable and xml_match:
        home_name = FOTMOB_NAME_OVERRIDES.get(xml_match.get("home", {}).get("name", "Home"), xml_match.get("home", {}).get("name", "Home"))
        away_name = FOTMOB_NAME_OVERRIDES.get(xml_match.get("away", {}).get("name", "Away"), xml_match.get("away", {}).get("name", "Away"))
        home_id   = xml_match.get("home", {}).get("id")
        away_id   = xml_match.get("away", {}).get("id")
        score_str = xml_match.get("status", {}).get("scoreStr", "0 - 0")
        utc_time  = xml_match.get("status", {}).get("utcTime", "")
        general   = {"matchId": xml_match.get("id", 0)}
    else:
        general   = fm_data.get("general", {})
        header    = fm_data.get("header", {})
        teams     = header.get("teams", [{}, {}])
        home_info = teams[0] if len(teams) > 0 else {}
        away_info = teams[1] if len(teams) > 1 else {}
        home_id   = home_info.get("id")
        away_id   = away_info.get("id")
        home_name = FOTMOB_NAME_OVERRIDES.get(home_info.get("name", "Home"), home_info.get("name", "Home"))
        away_name = FOTMOB_NAME_OVERRIDES.get(away_info.get("name", "Away"), away_info.get("name", "Away"))
        score_str = header.get("status", {}).get("scoreStr", "0 - 0")
        utc_time  = header.get("status", {}).get("utcTime", "")

    try:
        parts      = re.split(r"\s*-\s*", score_str)
        home_score = int(parts[0].strip())
        away_score = int(parts[1].strip())
    except Exception:
        home_score = away_score = 0

    try:
        dt = datetime.fromisoformat(utc_time.replace("Z", "+00:00"))
        date_str = dt.strftime("%Y-%m-%d")
    except Exception:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    venue_info = _parse_fotmob_venue(fm_data) if not fotmob_unavailable else {
        "venue": "", "city": "", "country": "England", "stage": "Premier League"
    }

    # ── Events & players ─────────────────────────────────────────────────────
    if ws_data and ws_data.get("events"):
        ws_home      = ws_data.get("home", {})
        ws_away      = ws_data.get("away", {})
        events       = ws_data["events"]
        home_tid     = ws_home.get("teamId", home_id)
        away_tid     = ws_away.get("teamId", away_id)
        home_players = ws_home.get("players", [])
        away_players = ws_away.get("players", [])
        # WhoScored has the real fulltime scores — use them
        ws_home_score = ws_home.get("scores", {}).get("fulltime")
        ws_away_score = ws_away.get("scores", {}).get("fulltime")
        if ws_home_score is not None:
            home_score = int(ws_home_score)
        if ws_away_score is not None:
            away_score = int(ws_away_score)
        # On a freshly-finished match WhoScored sometimes returns no fulltime
        # score, which would leave the FotMob stub's 0-0 in the header even
        # though the goals are in the event stream. Derive the score from the
        # goals instead — own goals are tagged with the scoring player's team,
        # so credit them to the opponent (matches the true result).
        if ws_home_score is None or ws_away_score is None:
            _gh = _ga = 0
            for _e in events:
                if _e.get("type", {}).get("displayName") != "Goal":
                    continue
                if "Shoot" in _e.get("period", {}).get("displayName", ""):
                    continue
                _quals = {q.get("type", {}).get("displayName", "")
                          for q in _e.get("qualifiers", [])}
                _tid = _e.get("teamId")
                if "OwnGoal" in _quals:
                    _tid = away_tid if _tid == home_tid else home_tid
                if _tid == home_tid:
                    _gh += 1
                elif _tid == away_tid:
                    _ga += 1
            if ws_home_score is None:
                home_score = _gh
            if ws_away_score is None:
                away_score = _ga
            log.info("WhoScored fulltime missing — derived score %d-%d from goal events",
                     home_score, away_score)
        # Patch names into WhoScored data if FotMob was unavailable
        if fotmob_unavailable:
            ws_home["name"] = home_name
            ws_away["name"] = away_name
        ws_home["scores"] = {"fulltime": home_score}
        ws_away["scores"] = {"fulltime": away_score}
        log.info("Using WhoScored events (%d)", len(events))
    elif not fotmob_unavailable:
        events       = _parse_fotmob_shots(fm_data, home_id, away_id)
        home_tid     = home_id
        away_tid     = away_id
        home_players = _parse_fotmob_lineup(fm_data, "home")
        away_players = _parse_fotmob_lineup(fm_data, "away")
        log.info("Using FotMob shot events only (%d)", len(events))
    else:
        log.error("No event data available (FotMob unavailable + no WhoScored data).")
        events       = []
        home_tid     = home_id
        away_tid     = away_id
        home_players = []
        away_players = []

    # Penalty shootout (drawn knockout tie): record the shootout result separately;
    # it is NOT folded into the match score or any shot stat.
    pk_home, pk_away = _shootout_score(events, home_tid, away_tid)
    if pk_home is not None:
        log.info("Penalty shootout: %s %d-%d %s", home_name, pk_home, pk_away, away_name)

    # ── Per-source stats → averaged match_stats ───────────────────────────────
    # Normalise every provider to the SAME canonical {stat:{home,away}} schema + units,
    # store each verbatim in stats_by_source (so all raw data is queryable), then average
    # overlapping stats. _compute_ws_stats already derives possession/passes/duels from
    # the WhoScored event stream, so no ad-hoc flat-key pass maths is needed any more.
    def _bc_missed(side_id):
        return sum(
            1 for e in events
            if e.get("teamId") == side_id
            and not _ev_is_shootout(e)
            and any(q.get("type", {}).get("displayName") == "BigChance"
                    for q in e.get("qualifiers", []))
            and e.get("type", {}).get("displayName") != "Goal"
        )

    stats_by_source: dict = {}
    fm_stats = _parse_fotmob_stats(fm_data) if not fotmob_unavailable else {}
    if fm_stats:
        stats_by_source[fm_data.get("_source_name", "fotmob")] = fm_stats
    if events:
        ws_stats = _compute_ws_stats(events, home_tid, away_tid)
        ws_stats["big_chances_missed"] = {"home": _bc_missed(home_tid),
                                          "away": _bc_missed(away_tid)}
        stats_by_source["whoscored"] = ws_stats

    match_stats = _merge_sources(stats_by_source)

    # Keep big_chances_missed even if FotMob was the only stats source.
    if "big_chances_missed" not in match_stats and events:
        match_stats["big_chances_missed"] = {"home": _bc_missed(home_tid),
                                             "away": _bc_missed(away_tid)}

    pid_name = {}
    for p in home_players + away_players:
        pid = p.get("playerId")
        if pid:
            pid_name[str(pid)] = p.get("name", "")

    return {
        "matchId":  general.get("matchId", 0),
        "meta": {
            "stage":      venue_info.get("stage", "Group Stage"),
            "group":      general.get("parentLeagueName", None),
            "venue":      venue_info.get("venue", ""),
            "city":       venue_info.get("city", ""),
            "country":    venue_info.get("country", "England"),
            "date":       date_str,
            "attendance": general.get("attendance"),
        },
        "home": {
            "teamId":  home_tid,
            "name":    home_name,
            "score":   home_score,
            "penalty_score": pk_home,
            "players": home_players,
            "stats":   {},
            "field":   "home",
        },
        "away": {
            "teamId":  away_tid,
            "name":    away_name,
            "score":   away_score,
            "penalty_score": pk_away,
            "players": away_players,
            "stats":   {},
            "field":   "away",
        },
        "events":      events,
        "match_stats": match_stats,
        # Verbatim per-source stat lines (FotMob, WhoScored, …) behind the averaged
        # match_stats above — so every provider's raw numbers are stored and queryable.
        "stats_by_source": stats_by_source,
        "playerIdNameDictionary": pid_name,
        "_scraped_at": datetime.now(timezone.utc).isoformat(),
        "_sources":    ([fm_data.get("_source_name", "fotmob")] if not fotmob_unavailable else [])
                       + (["whoscored"] if ws_data else []),
    }


# ══════════════════════════════════════════════════════════════════════════
# SAVE & TRIGGER
# ══════════════════════════════════════════════════════════════════════════

def _existing_file_for_id(fotmob_id) -> Path | None:
    """Find an already-saved match file carrying this same FotMob id.

    Knockout fixtures ship as slot-coded stubs (e.g. 2026_06_28_2A_vs_2B.json)
    so the dashboard bracket has something to link to before the teams are known.
    Once the tie is played FotMob returns the REAL team names, and naming the
    scraped file after them ("…_South_Africa_vs_Canada.json") would (a) leave the
    stub behind as a duplicate calendar row and (b) give the result an id the
    bracket can't recognise (the bracket keys off the "2A_vs_2B" slot code in the
    id). So we overwrite the stub in place — same filename, real content."""
    if not fotmob_id:
        return None
    want = str(fotmob_id)
    for f in sorted(MATCHES_DIR.glob("*.json")):
        if "_cache" in f.name:
            continue  # skip scraper WhoScored cache files
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        got = d.get("matchId") or d.get("match_id") or d.get("id")
        if got is not None and str(got) == want:
            return f
    return None


def _output_path(fotmob_id: int, season: str) -> Path:
    """Rich per-match JSON path: matches/<season>/<fotmob_id>.json. A league fixture has
    a stable FotMob id, so we name by id (no slot-code stubs like the WC knockouts)."""
    d = MATCHES_DIR / season
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{fotmob_id}.json"


def fetch_and_save(fotmob_id: int, season: str = "2025-26", fotmob_only: bool = False,
                   xml_match: dict | None = None,
                   out_path: "str | Path | None" = None) -> Path | None:
    """Full pipeline for one match: fetch (FotMob + WhoScored + Understat) → merge → save.

    Saves to matches/<season>/<fotmob_id>.json unless ``out_path`` forces a destination."""
    if fotmob_id in _fetched_ids:
        log.info("Match %d already fetched this session, skipping.", fotmob_id)
        return None

    fm_data = fotmob_fetch_match_details(fotmob_id)  # now returns stub if API is down

    # Resolve team names: prefer FotMob JSON, fall back to XML match data.
    # Apply FOTMOB_NAME_OVERRIDES here too so placeholder slot names (e.g.
    # "European Play-Off C") become the real qualifier before the WhoScored
    # slug search runs — otherwise the search can never match the real fixture.
    if fm_data.get("_fotmob_unavailable") and xml_match:
        home_name = xml_match.get("home", {}).get("name", "Home")
        away_name = xml_match.get("away", {}).get("name", "Away")
    else:
        teams     = fm_data.get("header", {}).get("teams", [{}, {}])
        home_name = teams[0].get("name", "Home") if teams else "Home"
        away_name = teams[1].get("name", "Away") if len(teams) > 1 else "Away"
    home_name = FOTMOB_NAME_OVERRIDES.get(home_name, home_name)
    away_name = FOTMOB_NAME_OVERRIDES.get(away_name, away_name)

    # Understat — the EPL's xG + shot-level source (replaces SofaScore). Attempt it for
    # every match to get xG / shots / rosters; disable with UNDERSTAT_FALLBACK=0. It's a
    # Selenium fetch, so it runs inside the same run-lock the WhoScored scrape holds.
    us_data = None
    if os.environ.get("UNDERSTAT_FALLBACK", "1") != "0":
        around = ""
        if xml_match:
            around = (xml_match.get("status", {}).get("utcTime", "") or "")[:10]
        try:
            from epl.understat import understat_fetch_match_details
            us_data = understat_fetch_match_details(home_name, away_name, around or None, season)
        except Exception as exc:
            log.warning("Understat fetch failed for %s vs %s: %s", home_name, away_name, exc)

    ws_data = None
    if not fotmob_only:
        ws_mid = whoscored_search_match_id(home_name, away_name)
        if ws_mid:
            ws_url  = _build_whoscored_url(home_name, away_name, ws_mid)
            ws_data = whoscored_fetch_match(ws_url)

    match_json = build_match_json(fm_data, ws_data, xml_match=xml_match)

    # Attach Understat: store its shot-level + roster block for the dashboard, record it as
    # a contributing source, and fill any xG/shot stats FotMob+WhoScored left empty. (Kept
    # additive so it can't corrupt the WhoScored-canonical event geometry / goals.)
    if us_data:
        match_json.setdefault("_sources", [])
        if "understat" not in match_json["_sources"]:
            match_json["_sources"].append("understat")
        match_json["_understat"] = us_data.get("_understat")
        ums = us_data.get("match_stats") or {}
        ms = match_json.setdefault("match_stats", {})
        for k, v in ums.items():
            if v is not None and ms.get(k) in (None, "", 0):
                ms[k] = v
        match_json.setdefault("stats_by_source", {})["understat"] = ums

    # Stamp season + matchday into meta so the renderer/builders can label the game.
    _meta = match_json.setdefault("meta", {})
    _meta["season"] = season
    _meta["competition"] = "Premier League"
    _md = next((m.get("matchday") for m in _load_schedule() if m.get("fotmob_id") == fotmob_id), None)
    if _md is not None:
        _meta["matchday"] = _md

    out_path   = Path(out_path) if out_path else _output_path(fotmob_id, season)

    # Preserve the knockout-round label from the slot stub. build_match_json defaults
    # the stage to "Group Stage" when FotMob is unavailable (WhoScored carries no stage),
    # which mislabels every KO tie. The slot stub the result overwrites knows the round.
    if out_path and Path(out_path).exists():
        try:
            _prev = json.loads(Path(out_path).read_text(encoding="utf-8"))
            _prev_stage = _prev.get("stage") or _prev.get("meta", {}).get("stage")
            if _prev_stage and _prev_stage != "Group Stage" and match_json.get("meta"):
                match_json["meta"]["stage"] = _prev_stage
        except Exception:
            pass

    # Never clobber a real saved match with an empty stub. A failed scrape (FotMob
    # down + WhoScored id not found) builds a stub with no events/lineups; writing
    # it over good data would destroy a published match AND leave the working tree
    # dirty so `git pull` aborts. If the new result is empty but the target already
    # holds a real scrape, keep the existing file and report failure instead.
    def _has_data(mj: dict) -> bool:
        return bool(mj.get("events")
                    or (mj.get("home") or {}).get("players")
                    or (mj.get("away") or {}).get("players"))

    if not _has_data(match_json) and out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if _has_data(existing):
            log.warning("Scrape produced no data for id=%s — keeping existing real file "
                        "%s (refusing to overwrite it with an empty stub).",
                        fotmob_id, out_path.name)
            return None

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(match_json, fh, indent=2)

    _fetched_ids.add(fotmob_id)
    log.info("Saved → %s", out_path)
    return out_path


# ══════════════════════════════════════════════════════════════════════════
# CONTINUOUS WATCHER
# ══════════════════════════════════════════════════════════════════════════

def watch_loop(fotmob_only: bool = False) -> None:
    """Poll FotMob every POLL_INTERVAL seconds for newly finished WC matches."""
    log.info("Watcher started. Polling every %ds for EPL matches …", POLL_INTERVAL)
    while True:
        try:
            matches = fotmob_fetch_wc_matches()
            for m in matches:
                status = m.get("status", {})
                # Only process matches that have finished
                if not status.get("finished", False):
                    continue
                mid = m.get("id")
                if mid and mid not in _fetched_ids:
                    # Check output file doesn't already exist
                    teams     = [m.get("home", {}), m.get("away", {})]
                    home_name = FOTMOB_NAME_OVERRIDES.get(teams[0].get("name", ""), teams[0].get("name", ""))
                    away_name = FOTMOB_NAME_OVERRIDES.get(teams[1].get("name", ""), teams[1].get("name", ""))
                    utc_time  = status.get("utcTime", "")
                    try:
                        date_str = datetime.fromisoformat(
                            utc_time.replace("Z", "+00:00")
                        ).strftime("%Y_%m_%d")
                    except Exception:
                        date_str = "*"
                    pattern = f"{date_str}_{home_name.replace(' ','_')}_vs_{away_name.replace(' ','_')}.json"
                    existing = list(MATCHES_DIR.glob(pattern))
                    if existing:
                        _fetched_ids.add(mid)
                        continue
                    log.info("New finished match: %s vs %s (id=%d)", home_name, away_name, mid)
                    fetch_and_save(mid, fotmob_only=fotmob_only, xml_match=m)

        except Exception as exc:
            log.error("Watch loop error: %s", exc)

        time.sleep(POLL_INTERVAL)


# ══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="EPL Match Scraper")
    parser.add_argument("--fotmob-id", type=int,
                        help="Fetch a specific FotMob match ID and exit.")
    parser.add_argument("--fotmob-only", action="store_true",
                        help="Skip WhoScored (FotMob data only, no pass networks).")
    args = parser.parse_args()

    if args.fotmob_id:
        # Try to find the xml_match stub from today/yesterday's feed
        xml_stub = None
        try:
            all_matches = fotmob_fetch_wc_matches()
            xml_stub = next((m for m in all_matches if m.get("id") == args.fotmob_id), None)
        except Exception:
            pass
        path = fetch_and_save(args.fotmob_id, fotmob_only=args.fotmob_only, xml_match=xml_stub)
        sys.exit(0 if path else 1)
    else:
        watch_loop(fotmob_only=args.fotmob_only)


if __name__ == "__main__":
    main()
