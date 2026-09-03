#!/usr/bin/env python3
"""Refresh laliga/understat_cache/<startYear>.json — one getLeagueData dump per season,
used by laliga_dashboard/build_data.py to show Understat's own xG next to ours and
FotMob's on the Data tab. One Selenium request per season (not per match), so a full
refresh across every season takes seconds, not the hour a per-match scrape would.

    py laliga/update_understat_cache.py                    # every season the dashboard has
    py laliga/update_understat_cache.py --season 2026-27    # just the current one

Safe to re-run any time (e.g. after a new matchday) — each season's file is replaced
wholesale, and build_data.py picks up the new numbers on its next run.
"""
from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE_DIR = os.path.join(HERE, "understat_cache")
SCHED_DIR = os.path.join(HERE, "schedules")

import sys
sys.path.insert(0, ROOT)
from laliga.understat import get_league_data, _new_driver  # noqa: E402


def _known_seasons():
    out = []
    if os.path.isdir(SCHED_DIR):
        for f in sorted(os.listdir(SCHED_DIR)):
            if f.startswith("SCHEDULE_") and f.endswith(".json"):
                out.append(f[len("SCHEDULE_"):-len(".json")])
    return out


def refresh(seasons):
    os.makedirs(CACHE_DIR, exist_ok=True)
    driver = _new_driver()
    try:
        for season in seasons:
            league = get_league_data(season, driver)
            if not league:
                print(f"{season}: FAILED (no data)")
                continue
            dates = league.get("dates", [])
            played = [m for m in dates if m.get("isResult")]
            start_year = season.split("-")[0]
            path = os.path.join(CACHE_DIR, f"{start_year}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(dates, fh, ensure_ascii=False)
            print(f"{season}: {len(played)}/{len(dates)} played -> {os.path.relpath(path, ROOT)}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", help="'2025-26' style; default: every season with a schedule file")
    args = ap.parse_args()
    refresh([args.season] if args.season else _known_seasons())
