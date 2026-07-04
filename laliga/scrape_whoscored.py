#!/usr/bin/env python3
"""
Harvest La Liga rich match data from WhoScored and map it onto the FotMob schedule.

Why this exists: WhoScored ids are NOT contiguous or chronological, so we can't range-scrape.
Individual `/Matches/<id>/Live` pages DO load reliably (via plain Selenium), but the fixtures
*listing* is flaky and paginated by matchday. So we: (1) harvest match ids from the La Liga
fixtures page, retrying and clicking back through matchdays; (2) scrape each match's
`matchCentreData`; (3) map it to a fixture in `schedules/SCHEDULE_<season>.json` by team names
(ordered, so the right home/away leg) and save it as `matches/<season>/<fotmob_id>.json` via the
shared `build_match_json` — the exact shape the dashboard builders consume. Resumable (skips
matches already saved with events).

Usage:
    py laliga/scrape_whoscored.py --season 2025-26                 # full season
    py laliga/scrape_whoscored.py --season 2025-26 --max-back 20   # navigate more matchdays
    py laliga/scrape_whoscored.py --season 2025-26 --ids 1914252,1914253   # specific ids
"""
from __future__ import annotations

import os, re, sys, json, time, argparse, unicodedata
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from laliga.scraper import build_match_json, _fotmob_unavailable_stub, LALIGA_WS_BASES

SCHED_DIR = _REPO / "laliga" / "schedules"
MATCH_DIR = _REPO / "laliga" / "matches"


def _key(name: str) -> str:
    # Accent-fold + lowercase, strip SAFE club-suffix noise, alnum only. Critically do NOT
    # strip "real": collapsing "Real Madrid" -> "madrid" makes it substring-match
    # "atleticoMADRID" and scrambles the two Madrid clubs. Keeping "real" leaves
    # "realmadrid" vs "atleticomadrid" distinct. Contains-matching still handles short/long
    # variants (WhoScored "Betis"/"Alaves"/"Athletic Bilbao" vs schedule "Real Betis"/
    # "Deportivo Alaves"/"Athletic Club").
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    for j in ("deportivo ", "rcd ", "cd ", "cf ", "ud ", "sd ", "club", " balompie", " fc"):
        s = s.replace(j, " ")
    return re.sub(r"[^a-z0-9]", "", s)


def _exact_match(ws_h, ws_a, sc_h, sc_a):
    return _key(ws_h) == _key(sc_h) and _key(ws_a) == _key(sc_a)


def _teams_match(ws_h, ws_a, sc_h, sc_a):
    kh, ka, sh, sa = _key(ws_h), _key(ws_a), _key(sc_h), _key(sc_a)
    def ov(x, y): return x and y and (x == y or x in y or y in x)
    return ov(kh, sh) and ov(ka, sa)


def load_schedule(season):
    data = json.loads((SCHED_DIR / f"SCHEDULE_{season}.json").read_text(encoding="utf-8"))
    return data.get("matches", [])


def _plain_driver():
    from selenium import webdriver
    o = webdriver.ChromeOptions()
    if os.environ.get("LALIGA_VISIBLE") != "1":
        o.add_argument("--headless=new")
    for a in ("--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1000",
              "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"):
        o.add_argument(a)
    d = webdriver.Chrome(options=o)
    d.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return d


def make_driver():
    # Plain Selenium (Selenium Manager auto-resolves the driver) is the default here —
    # undetected-chromedriver breaks on Chrome 149. Opt into uc with LALIGA_USE_UC=1.
    if os.environ.get("LALIGA_USE_UC") == "1":
        try:
            import undetected_chromedriver as uc
            o = uc.ChromeOptions()
            if os.environ.get("LALIGA_VISIBLE") != "1":
                o.add_argument("--headless=new")
            for a in ("--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1000"):
                o.add_argument(a)
            return uc.Chrome(options=o)
        except Exception as exc:
            print(f"  (undetected unavailable: {exc}; using plain selenium)")
    return _plain_driver()


_MCD = "matchCentreData:"


def extract_mcd(html):
    i = html.find(_MCD)
    if i < 0:
        return None
    seg = html[i + len(_MCD):].split("matchCentreEventTypeJson")[0].strip().rstrip(",")
    try:
        return json.loads(seg)
    except Exception:
        # brace-match fallback
        depth = 0
        for j, ch in enumerate(seg):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(seg[:j + 1])
                    except Exception:
                        return None
        return None


def harvest_ids(driver, max_back, want=None):
    """Collect La Liga match ids from the fixtures page, paging back through matchdays."""
    from selenium.webdriver.common.by import By
    ids = []
    for base in LALIGA_WS_BASES:
        got_page = False
        for attempt in range(6):
            driver.get(base)
            time.sleep(9)
            found = re.findall(r"/[Mm]atches/(\d+)/", driver.page_source)
            if found:
                got_page = True
                break
            print(f"  fixtures load attempt {attempt+1} empty, retrying…")
        if not got_page:
            print("  fixtures page never yielded ids on", base)
            continue
        seen = set()
        empty_streak = 0
        # The calendar is a WEEKLY view; #dayChangeBtn-prev steps back one week. La Liga
        # plays ~1 round/week, so page back across the whole season (~40+ weeks).
        for step in range(max_back + 1):
            page_ids = list(dict.fromkeys(re.findall(r"/[Mm]atches/(\d+)/", driver.page_source)))
            new = [i for i in page_ids if i not in seen]
            for i in new:
                seen.add(i); ids.append(i)
            print(f"  week {step} ({_cal_label(driver)}): +{len(new)} ids (total {len(ids)})")
            empty_streak = empty_streak + 1 if not new else 0
            if empty_streak >= 16:
                print("  6 empty weeks in a row — reached season start, stopping.")
                break
            if want and len(ids) >= want:
                break
            clicked = False
            for sel in ["#dayChangeBtn-prev", "button.Calendar-module_dayChangeBtn__sEvC8",
                        "[id='dayChangeBtn-prev']", "a.previous"]:
                try:
                    for e in driver.find_elements(By.CSS_SELECTOR, sel):
                        if e.is_displayed():
                            driver.execute_script("arguments[0].click();", e)
                            clicked = True
                            break
                    if clicked:
                        break
                except Exception:
                    continue
            if not clicked:
                print("  previous-week control not found; stopping pagination.")
                break
            time.sleep(5)
    return list(dict.fromkeys(ids))


def _cal_label(driver):
    from selenium.webdriver.common.by import By
    try:
        e = driver.find_elements(By.CSS_SELECTOR, "#toggleCalendar, .Calendar-module_controller__Ke8vm")
        return (e[0].text or "").strip()[:16] if e else ""
    except Exception:
        return ""


def scrape_one(driver, wsid, tries=2):
    for t in range(tries):
        driver.get(f"https://www.whoscored.com/Matches/{wsid}/Live")
        time.sleep(9 + 3 * t)
        mcd = extract_mcd(driver.page_source)
        if mcd and mcd.get("events"):
            return mcd
    return None


def save_match(mcd, fixture, season):
    fid = fixture["fotmob_id"]
    ws_h = (mcd.get("home") or {}).get("name", "")
    ws_a = (mcd.get("away") or {}).get("name", "")
    hs = (mcd.get("home") or {}).get("scores", {}).get("fulltime")
    as_ = (mcd.get("away") or {}).get("scores", {}).get("fulltime")
    score = f"{hs} - {as_}" if hs is not None and as_ is not None else "0 - 0"
    xml_stub = {"id": fid, "home": {"name": fixture["home"], "id": fixture.get("home_id")},
                "away": {"name": fixture["away"], "id": fixture.get("away_id")},
                "status": {"scoreStr": score, "utcTime": (fixture.get("kickoff_utc") or ""), "finished": True}}
    mj = build_match_json(_fotmob_unavailable_stub(), mcd, xml_match=xml_stub)
    meta = mj.setdefault("meta", {})
    meta["season"] = season
    meta["competition"] = "LaLiga"
    if fixture.get("matchday"):
        meta["matchday"] = fixture["matchday"]
    out = MATCH_DIR / season / f"{fid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(mj, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def already_done(season, fid):
    p = MATCH_DIR / season / f"{fid}.json"
    if not p.exists():
        return False
    try:
        return bool(json.loads(p.read_text(encoding="utf-8")).get("events"))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--ids", help="comma-separated WhoScored ids (skip fixtures harvest)")
    ap.add_argument("--max-back", type=int, default=40, help="matchday pages to navigate back")
    ap.add_argument("--limit", type=int, help="stop after saving N matches")
    args = ap.parse_args()

    schedule = load_schedule(args.season)
    print(f"Season {args.season}: {len(schedule)} fixtures in schedule.")
    driver = make_driver()
    saved = skipped = unmatched = failed = 0
    try:
        if args.ids:
            ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        else:
            print("Harvesting match ids from WhoScored fixtures…")
            ids = harvest_ids(driver, args.max_back)
        print(f"Got {len(ids)} candidate WhoScored ids. Scraping…")
        for n, wsid in enumerate(ids, 1):
            mcd = scrape_one(driver, wsid)
            if not mcd:
                failed += 1
                print(f"[{n}/{len(ids)}] {wsid}: no data")
                continue
            wh = (mcd.get("home") or {}).get("name", "")
            wa = (mcd.get("away") or {}).get("name", "")
            # Exact team-key match first (so "Real Madrid" never grabs an Atletico fixture),
            # then fall back to contains-matching for short/long name variants.
            fixture = (next((f for f in schedule if _exact_match(wh, wa, f["home"], f["away"])), None)
                       or next((f for f in schedule if _teams_match(wh, wa, f["home"], f["away"])), None))
            if not fixture:
                unmatched += 1
                print(f"[{n}/{len(ids)}] {wsid}: {wh} vs {wa} — not in {args.season} schedule, skip")
                continue
            if already_done(args.season, fixture["fotmob_id"]):
                skipped += 1
                continue
            out = save_match(mcd, fixture, args.season)
            saved += 1
            print(f"[{n}/{len(ids)}] {wsid}: {wh} {(mcd.get('home') or {}).get('scores',{}).get('fulltime')}-"
                  f"{(mcd.get('away') or {}).get('scores',{}).get('fulltime')} {wa} -> {out.name} (MD{fixture.get('matchday')})")
            if args.limit and saved >= args.limit:
                break
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    print(f"\nDone: {saved} saved, {skipped} already had data, {unmatched} unmatched, {failed} failed.")


if __name__ == "__main__":
    main()
