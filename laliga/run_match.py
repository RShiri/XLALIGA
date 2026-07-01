"""
FIFA World Cup 2026 – Combined one-shot match runner.

Does the entire flow for ONE match in a single synchronous call:
    scrape (WhoScored via FotMob id) → render PNG → push to GitHub → post to X.

Unlike pipeline.py (a long-running watcher with a delayed-tweet thread), this
module runs start-to-finish and exits. That makes it ideal for Windows Task
Scheduler: register one task per match firing at (kick-off + 2h), each calling

    py -m laliga.run_match --fotmob-id <ID>

Usage:
    py -m laliga.run_match --fotmob-id 4667812
    py -m laliga.run_match --fotmob-id 4667812 --fotmob-only   # skip WhoScored
    py -m laliga.run_match --fotmob-id 4667812 --no-post       # render+push only
    py -m laliga.run_match --fotmob-id 4667812 --no-push       # render+post only
    py -m laliga.run_match --from-file laliga/matches/x.json    # skip scraping
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import argparse
from pathlib import Path

# ── Bootstrap path + env ──────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

from laliga.scraper     import (fetch_and_save, fotmob_fetch_wc_matches,
                                schedule_team_names, schedule_lookup_by_teams)
from laliga.renderer    import render_wc_dashboard, output_filename
from laliga.git_ops     import push_png_to_xworldcuptwit, push_match_update
from laliga._runlock    import scrape_lock

log = logging.getLogger("laliga.run_match")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RUN] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_REPO_ROOT / "laliga" / "run_match.log", encoding="utf-8"),
    ],
)

# scraper.py calls logging.basicConfig() at import time (above imports run first),
# so the basicConfig() here is a no-op and its FileHandler never attaches — which
# is why scheduled-run crashes left run_match.log empty and undiagnosable. Force a
# file handler onto the root logger so every run (especially failures) is recorded.
_root_logger = logging.getLogger()
if not any(isinstance(h, logging.FileHandler) for h in _root_logger.handlers):
    _file_handler = logging.FileHandler(_REPO_ROOT / "laliga" / "run_match.log", encoding="utf-8")
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s [RUN] %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    _root_logger.addHandler(_file_handler)

# Scrape robustness. The WhoScored step launches a flaky headless browser
# (undetected-chromedriver throws transient WinError 6 / Cloudflare blocks). A
# single crash there used to abort the whole match with exit 1 and NO retry,
# silently leaving that game unpublished forever (the task already "ran", so
# StartWhenAvailable never re-fires it). Retry the scrape before giving up.
SCRAPE_ATTEMPTS    = 3
SCRAPE_RETRY_DELAY = 20  # seconds between attempts

OUTPUT_DIR = _REPO_ROOT / "laliga" / "output"


def send_whatsapp_notification(image_url: str, text: str) -> bool:
    """Send match notification with dashboard image link to WhatsApp."""
    provider = os.environ.get("WHATSAPP_PROVIDER", "").lower()
    phone = os.environ.get("WHATSAPP_PHONE")
    
    if not phone:
        log.warning("WhatsApp notification skipped: WHATSAPP_PHONE not set in .env.")
        return False
        
    if provider == "twilio":
        sid = os.environ.get("WHATSAPP_TWILIO_SID")
        token = os.environ.get("WHATSAPP_TWILIO_TOKEN")
        from_num = os.environ.get("WHATSAPP_TWILIO_FROM", "whatsapp:+14155238886")
        
        if not sid or not token:
            log.warning("WhatsApp Twilio skipped: SID or Token not configured in .env.")
            return False
            
        try:
            from twilio.rest import Client
            client = Client(sid, token)
            message = client.messages.create(
                body=text,
                media_url=[image_url],
                from_=from_num,
                to=f"whatsapp:{phone}"
            )
            log.info("WhatsApp sent via Twilio: SID=%s", message.sid)
            return True
        except ImportError:
            log.error("WhatsApp Twilio failed: 'twilio' package not installed. Run 'pip install twilio'")
            return False
        except Exception as e:
            log.error("WhatsApp Twilio failed: %s", e)
            return False
            
    elif provider == "callmebot":
        key = os.environ.get("WHATSAPP_CALLMEBOT_KEY")
        if not key:
            log.warning("WhatsApp CallMeBot skipped: WHATSAPP_CALLMEBOT_KEY not set.")
            return False
            
        try:
            import urllib.parse
            import urllib.request
            
            msg = f"{text}\n\nView Dashboard: {image_url}"
            encoded_msg = urllib.parse.quote_plus(msg)
            url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded_msg}&apikey={key}"
            
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as response:
                resp_text = response.read().decode("utf-8")
                if "success" in resp_text.lower() or response.status == 200:
                    log.info("WhatsApp sent via CallMeBot.")
                    return True
                else:
                    log.error("WhatsApp CallMeBot response: %s", resp_text)
                    return False
        except Exception as e:
            log.error("WhatsApp CallMeBot failed: %s", e)
            return False
            
    else:
        log.warning("WhatsApp skipped: WHATSAPP_PROVIDER must be 'twilio' or 'callmebot' in .env.")
        return False


def _build_xml_stub(fotmob_id: int, home: str, away: str, date: str) -> dict:
    return {
        "id":   fotmob_id,
        "home": {"name": home, "id": None},
        "away": {"name": away, "id": None},
        "status": {
            "scoreStr": "0 - 0",
            "utcTime":  f"{date}T12:00:00+00:00" if date else "",
            "finished": True,
        },
    }


def run_match(
    fotmob_id: int | None = None,
    from_file: str | None = None,
    home_name: str | None = None,
    away_name: str | None = None,
    *,
    season: str = "2025-26",
    fotmob_only: bool = False,
    do_push: bool = True,
    do_whatsapp: bool = True,
) -> bool:
    """
    Full single-match flow. Returns True on success.
    Provide one of: fotmob_id, from_file, or home_name+away_name.
    Team names are resolved (in order): FotMob XML → season schedule → CLI args.
    """
    # ── 1. Acquire match JSON ─────────────────────────────────────────────
    if from_file:
        json_path = Path(from_file)
        if not json_path.exists():
            log.error("Match file not found: %s", json_path)
            return False
        log.info("Using existing match file: %s", json_path.name)
    elif fotmob_id is not None or (home_name and away_name):
        # Resolve fotmob_id from team names if not given directly
        date_str = ""
        if fotmob_id is None:
            fotmob_id, date_str = schedule_lookup_by_teams(home_name, away_name)
            if fotmob_id is None:
                log.error("Could not find '%s vs %s' in schedule.", home_name, away_name)
                return False
            log.info("Resolved fotmob_id=%d for %s vs %s", fotmob_id, home_name, away_name)

        log.info("Scraping match id=%d (season %s) …", fotmob_id, season)

        # Resolve team names + date for the WhoScored search: FotMob XML first
        # (token-free), then the season schedule. (No knockout self-heal — a league
        # fixture already has real team names and a stable FotMob id.)
        xml_stub = None
        try:
            xml_stub = next(
                (m for m in fotmob_fetch_wc_matches() if m.get("id") == fotmob_id), None,
            )
            if xml_stub:
                log.info("FotMob XML: resolved %s vs %s",
                         xml_stub["home"]["name"], xml_stub["away"]["name"])
        except Exception as exc:
            log.warning("FotMob XML unavailable: %s", exc)

        if xml_stub is None:
            sched_home, sched_away, sched_date = schedule_team_names(fotmob_id)
            h = home_name or sched_home
            a = away_name or sched_away
            d = date_str or sched_date
            if h and a:
                log.info("Schedule fallback: %s vs %s", h, a)
                xml_stub = _build_xml_stub(fotmob_id, h, a, d)
            else:
                log.warning("No team names found — WhoScored search may fail.")

        json_path = None
        last_exc: Exception | None = None
        # Serialise the browser scrape across processes — undetected-chromedriver patches
        # a SHARED chromedriver, so two scrapers launching at once collide. See _runlock.py.
        with scrape_lock():
            for attempt in range(1, SCRAPE_ATTEMPTS + 1):
                try:
                    json_path = fetch_and_save(
                        fotmob_id, season, fotmob_only=fotmob_only, xml_match=xml_stub,
                    )
                    if json_path:
                        break
                    log.warning("Scrape attempt %d/%d returned no data for id=%d.",
                                attempt, SCRAPE_ATTEMPTS, fotmob_id)
                except Exception as exc:           # browser/network flake — retry
                    last_exc = exc
                    log.warning("Scrape attempt %d/%d crashed for id=%d: %s",
                                attempt, SCRAPE_ATTEMPTS, fotmob_id, exc, exc_info=True)
                if attempt < SCRAPE_ATTEMPTS:
                    time.sleep(SCRAPE_RETRY_DELAY)
        if not json_path:
            log.error("Scrape failed for id=%d after %d attempts (last error: %s) — aborting.",
                      fotmob_id, SCRAPE_ATTEMPTS, last_exc)
            return False
        log.info("Scraped → %s", json_path)
    else:
        log.error("Provide --fotmob-id, --match 'Home vs Away', or --from-file.")
        return False

    # ── 2. Load data ──────────────────────────────────────────────────────
    try:
        with open(json_path, encoding="utf-8") as fh:
            match_data = json.load(fh)
    except Exception as exc:
        log.error("Cannot read %s: %s", json_path, exc)
        return False

    home = match_data.get("home", {}).get("name", "Home")
    away = match_data.get("away", {}).get("name", "Away")
    home_score = match_data.get("home", {}).get("score", 0)
    away_score = match_data.get("away", {}).get("score", 0)

    # ── 3. Render dashboard PNG ───────────────────────────────────────────
    # Name the PNG (and the match-centre detail + the pushed raw JSON) after the SOURCE
    # json file. For a knockout game that's the slot-coded id (e.g. 2026_06_28_2A_vs_2B)
    # the dashboard bracket/calendar key on — naming by the real teams instead would make
    # find_png() 404 and leave the raw JSON unpushed. Group games are unaffected (their
    # filename already IS the real-team name).
    match_id = Path(json_path).stem
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        png_path = os.path.join(str(OUTPUT_DIR), match_id + ".png")
        render_wc_dashboard(match_data, png_path)
        log.info("PNG rendered → %s", png_path)
    except Exception as exc:
        log.error("Render failed for %s vs %s: %s", home, away, exc)
        return False

    # ── 4. Push PNG + regenerated web dashboard to XWORLDCUPTWIT (non-fatal) ─
    # render_wc_dashboard() already refreshed the local dashboard files, so this
    # commit makes the live website (and the PNG) update for the new match.
    raw_url = None
    if do_push:
        try:
            raw_url = push_match_update(
                png_path,
                match_id=match_id,
                commit_message=f"[LALIGA] {home} vs {away} analytics dashboard",
            )
            log.info("Match update pushed (PNG + site) → %s", raw_url)
        except Exception as exc:
            log.error("Git push failed (continuing): %s", exc)
    else:
        log.info("Skipping Git push (--no-push).")

    # ── 5. Send to WhatsApp ───────────────────────────────────────────────
    if do_whatsapp:
        if not raw_url:
            # Fallback to direct raw github URL pattern if push is disabled or failed
            raw_url = f"https://raw.githubusercontent.com/RShiri/XWORLDCUPTWIT/main/laliga_png/{os.path.basename(png_path)}"

        md = match_data.get("meta", {}).get("matchday")
        tag = f"Matchday {md}" if md else "La Liga"
        msg = f"⚽ La Liga Match Report ({tag}) · {season}\n🏆 {home} {home_score} - {away_score} {away}"
        send_whatsapp_notification(raw_url, msg)
    else:
        log.info("Skipping WhatsApp notification.")

    log.info("DONE: %s vs %s", home, away)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="La Liga one-shot: scrape (FotMob+WhoScored+Understat) → render → push → whatsapp.",
        epilog=(
            "Examples:\n"
            "  py -m laliga.run_match --fotmob-id 4837123 --season 2025-26\n"
            "  py -m laliga.run_match --match 'Barcelona vs Real Madrid' --season 2025-26\n"
            "  py -m laliga.run_match --from-file laliga/matches/2025-26/4837123.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--fotmob-id", type=int,
                     help="FotMob match ID (team names resolved from schedule if FotMob is down).")
    src.add_argument("--match", metavar="'Home vs Away'",
                     help="Match as team names, e.g. 'Barcelona vs Real Madrid'. Looks up fotmob-id automatically.")
    src.add_argument("--from-file",
                     help="Path to an existing match JSON (skip scraping entirely).")

    parser.add_argument("--season", default="2025-26", help="Season, e.g. 2025-26 or 2026-27.")
    parser.add_argument("--fotmob-only", action="store_true",
                        help="Skip WhoScored (FotMob shot data only).")
    parser.add_argument("--no-push", action="store_true",
                        help="Don't push the PNG to GitHub.")
    parser.add_argument("--no-post", action="store_true",
                        help="Don't send to WhatsApp.")
    args = parser.parse_args()

    home_name = away_name = None
    if args.match:
        parts = [p.strip() for p in args.match.split(" vs ", 1)]
        if len(parts) != 2 or not all(parts):
            log.error("--match must be in format 'Home vs Away'")
            sys.exit(1)
        home_name, away_name = parts

    ok = run_match(
        fotmob_id=args.fotmob_id,
        from_file=args.from_file,
        home_name=home_name,
        away_name=away_name,
        season=args.season,
        fotmob_only=args.fotmob_only,
        do_push=not args.no_push,
        do_whatsapp=not args.no_post,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
