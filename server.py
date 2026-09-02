#!/usr/bin/env python3
"""Local dashboard server: static site + the Scraper control API.

    py server.py                 → http://localhost:8778/laliga_dashboard/index.html
    py server.py --no-control    → static files only (the old behaviour)
    py server.py --port 9000

Serving the files is unchanged from before; what's new is a small **control API** under
``/api/`` that lets the dashboard's **Scraper** button run the pipeline instead of waiting for
the Windows scheduled tasks:

    GET  /api/health          is a control server here? (the button hides itself if not)
    GET  /api/state           seasons, fixture/scrape counts, last job
    POST /api/scrape          start a job   {action, season, ids, limit, matchday, push}
    GET  /api/job             current/last job status
    GET  /api/job/log?offset= incremental output
    POST /api/job/stop        stop the running job
    GET  /api/progress        recent PROGRESS.md entries
    POST /api/progress        add a note   {kind: platform|worked|failed, text}

Safety: the browser never supplies a command. It picks an **action name** from a fixed list and
this module builds the argv itself. The server binds 127.0.0.1 (loopback only) unless
``LALIGA_CONTROL_HOST`` says otherwise, and honours an optional shared secret in
``LALIGA_CONTROL_TOKEN`` (sent by the UI as ``X-Control-Token``).

Every job finishes by appending a row to PROGRESS.md via ``laliga.progress_log``.
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import shlex
import argparse
import mimetypes
import threading
import subprocess
import http.server
import socketserver
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "laliga" / "logs"
SCHED_DIR = ROOT / "laliga" / "schedules"
MATCH_DIR = Path(os.environ.get("LALIGA_MATCH_DIR") or (ROOT / "laliga" / "matches"))

PORT = int(os.environ.get("LALIGA_PORT", "8778"))
HOST = os.environ.get("LALIGA_CONTROL_HOST", "127.0.0.1")
TOKEN = os.environ.get("LALIGA_CONTROL_TOKEN", "").strip()
SITE_ORIGINS = ["https://rshiri.github.io"]

# Explicitly register MIME types to bypass any corrupt Windows Registry configurations
mimetypes.add_type('text/html', '.html')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('image/png', '.png')
mimetypes.add_type('image/svg+xml', '.svg')


# ─────────────────────────── job runner ───────────────────────────

class Job:
    """One pipeline run: a sequence of subprocesses, streamed line by line to the browser."""

    def __init__(self, label: str, steps: list[list[str]], season: str, target: str,
                 optional: "set[int] | tuple" = ()):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.label = label
        self.steps = steps
        self.optional = set(optional)      # steps allowed to fail without stopping the job
        self.season = season
        self.target = target
        self.lines: deque[str] = deque(maxlen=4000)
        self.dropped = 0                      # lines aged out of the deque
        self.started = time.time()
        self.finished: float | None = None
        self.returncode: int | None = None
        self.proc: subprocess.Popen | None = None
        self.stopped = False
        self.log_path = LOG_DIR / f"job_{self.id}.log"
        self._lock = threading.Lock()

    # -- output -------------------------------------------------------
    def emit(self, line: str) -> None:
        with self._lock:
            if len(self.lines) == self.lines.maxlen:
                self.dropped += 1
            self.lines.append(line.rstrip("\n"))
        try:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    def tail(self, offset: int) -> tuple[list[str], int]:
        """Lines after ``offset`` in absolute numbering, plus the new absolute offset."""
        with self._lock:
            base = self.dropped
            total = base + len(self.lines)
            start = max(offset - base, 0)
            return list(self.lines)[start:], total

    @property
    def running(self) -> bool:
        return self.finished is None

    def state(self) -> dict:
        return {
            "id": self.id, "label": self.label, "season": self.season, "target": self.target,
            "running": self.running, "returncode": self.returncode,
            "started": self.started, "finished": self.finished,
            "elapsed": round((self.finished or time.time()) - self.started),
            "stopped": self.stopped, "log": str(self.log_path),
        }

    # -- execution ----------------------------------------------------
    def run(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        rc = 0
        try:
            for index, step in enumerate(self.steps):
                if self.stopped:
                    break
                self.emit(f"$ {' '.join(shlex.quote(p) for p in step)}")
                self.proc = subprocess.Popen(
                    step, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1,
                )
                for line in self.proc.stdout:            # type: ignore[union-attr]
                    self.emit(line)
                rc = self.proc.wait()
                if rc != 0 and step[:2] == ["git", "commit"] and self._nothing_to_commit():
                    # "nothing to commit" isn't a failure — the site is simply up to date.
                    self.emit("[nothing to commit — already up to date]")
                    rc = 0
                    continue
                if rc != 0 and index in self.optional:
                    # e.g. the fixture refresh when FotMob is unreachable — the scrape can
                    # still run against the schedule already on disk.
                    self.emit(f"[optional step exited {rc} — continuing anyway]")
                    rc = 0
                    continue
                if rc != 0:
                    self.emit(f"[step exited {rc} — stopping]")
                    break
        except Exception as exc:                          # a broken command shouldn't kill the server
            rc = -1
            self.emit(f"[server error: {exc}]")
        finally:
            self.returncode = rc
            self.finished = time.time()
            self.proc = None
            self._journal()

    def _nothing_to_commit(self) -> bool:
        with self._lock:
            tail = list(self.lines)[-6:]
        return any("nothing to commit" in ln or "nothing added to commit" in ln for ln in tail)

    def stop(self) -> None:
        self.stopped = True
        proc = self.proc
        if proc and proc.poll() is None:
            self.emit("[stop requested]")
            try:
                proc.terminate()
            except Exception:
                pass

    # -- PROGRESS.md --------------------------------------------------
    _SUMMARIES = (
        # scrape_whoscored.py: "Done: 9 saved, 3 already had data, 0 unmatched, 1 failed."
        re.compile(r"Done:\s*(?P<saved>\d+) saved,\s*(?P<skipped>\d+) already had data,"
                   r"\s*(?P<unmatched>\d+) unmatched,\s*(?P<failed>\d+) failed"),
        # backfill.py: "Backfill done: 9 ok, 1 failed."
        re.compile(r"Backfill done:\s*(?P<saved>\d+) ok,\s*(?P<failed>\d+) failed"),
    )

    def _counts(self) -> dict:
        with self._lock:
            text = "\n".join(self.lines)
        for pattern in self._SUMMARIES:
            hits = list(pattern.finditer(text))
            if hits:
                got = hits[-1].groupdict()
                return {k: int(v) for k, v in got.items() if v is not None}
        return {}

    def _journal(self) -> None:
        """Append this run to PROGRESS.md — the whole point of the journal."""
        try:
            sys.path.insert(0, str(ROOT))
            from laliga.progress_log import log_scrape
        except Exception:
            return
        counts = self._counts()
        note = ""
        if counts.get("unmatched"):
            note = f"{counts['unmatched']} WhoScored id(s) not in the schedule"
        if self.stopped:
            note = ("stopped by user; " + note).strip("; ")
        elif self.returncode not in (0, None):
            with self._lock:
                last = next((ln for ln in reversed(self.lines) if ln.strip()), "")
            note = (f"exit {self.returncode} — {last[:140]}; " + note).strip("; ")
        ok = self.returncode == 0 and not self.stopped
        log_scrape(
            season=self.season, target=self.target,
            saved=counts.get("saved", 0), failed=counts.get("failed", 0),
            skipped=counts.get("skipped", 0),
            duration_s=(self.finished or time.time()) - self.started,
            trigger="dashboard button", note=note, ok=ok,
        )


_job_lock = threading.Lock()
_current: Job | None = None


# ─────────────────────────── actions ───────────────────────────

PY = sys.executable or "python"

SEASON_RE = re.compile(r"^\d{4}-\d{2}$")
IDS_RE = re.compile(r"^\d[\d,\s]*$")

REBUILD_STEPS = [
    [PY, "laliga_dashboard/build_match_details.py"],
    [PY, "laliga_dashboard/build_players.py"],
    [PY, "laliga_dashboard/build_shots.py"],
    [PY, "laliga_dashboard/build_player_lab.py"],
    [PY, "laliga_dashboard/build_database.py"],
    [PY, "laliga_dashboard/build_data.py"],
    [PY, "laliga_dashboard/build_split.py"],
]

ACTIONS = {
    "results_only": "Results & table only — no browser, ~1 minute (safe on any machine)",
    "schedule":     "Refresh fixtures & results (FotMob, no browser)",
    "scrape_new":   "Scrape every played match not scraped yet",
    "scrape_partial": "Re-scrape matches that only got FotMob data (no maps/lineups)",
    "scrape_ids":   "Scrape specific WhoScored id(s)",
    "scrape_match": "Scrape one match by FotMob id",
    "rebuild":      "Rebuild dashboard data from what's already scraped",
    "deploy":       "Commit & push the generated files to GitHub",
}


def build_job(body: dict) -> Job:
    """Translate a request into a whitelisted argv sequence. Raises ValueError on bad input."""
    action = str(body.get("action", "")).strip()
    if action not in ACTIONS:
        raise ValueError(f"unknown action '{action}'")

    season = str(body.get("season", "")).strip()
    if not SEASON_RE.match(season):
        raise ValueError("season must look like 2026-27")
    if not (SCHED_DIR / f"SCHEDULE_{season}.json").exists() and action != "schedule":
        raise ValueError(f"no schedule for {season} — run 'Refresh fixtures' first")

    push = bool(body.get("push"))
    optional: set[int] = set()
    limit = body.get("limit")
    matchday = body.get("matchday")
    steps: list[list[str]] = []
    target = ACTIONS[action]

    if action == "results_only":
        # Scores, table, fixtures and projection — one HTTP request, no Chrome, no scraping.
        # This is the low-risk update: it can't hang, thrash the machine or take an hour.
        steps = [[PY, "laliga/build_schedule.py", "--season", season],
                 [PY, "laliga_dashboard/build_data.py"]]
        target = "results & table"

    elif action == "schedule":
        steps = [[PY, "laliga/build_schedule.py", "--season", season],
                 [PY, "laliga_dashboard/build_data.py"]]

    elif action == "scrape_new":
        # One click = refresh the fixture list, then scrape whatever is still missing. The
        # refresh is optional: if FotMob is down, scrape against the schedule we already have.
        steps = [[PY, "laliga/build_schedule.py", "--season", season]]
        optional = {0}
        cmd = [PY, "laliga/backfill.py", "--season", season]
        if limit:
            cmd += ["--limit", str(int(limit))]
            target += f" (max {int(limit)})"
        if matchday:
            cmd += ["--matchday", str(int(matchday))]
            target = f"matchday {int(matchday)}"
        steps.append(cmd)

    elif action == "scrape_partial":
        steps = [[PY, f"laliga/backfill.py", "--season", season, "--redo-partial"]]
        target = "matches missing their WhoScored event stream"

    elif action == "scrape_ids":
        ids = str(body.get("ids", "")).strip()
        if not IDS_RE.match(ids):
            raise ValueError("ids must be digits separated by commas")
        ids = ",".join(part.strip() for part in ids.split(",") if part.strip())
        steps = [[PY, "laliga/scrape_whoscored.py", "--season", season, "--ids", ids]] + REBUILD_STEPS
        target = f"WhoScored ids {ids}"

    elif action == "scrape_match":
        raw = str(body.get("ids", "")).strip()
        if not raw.isdigit():
            raise ValueError("give a single numeric FotMob match id")
        # Always --no-push here: the shared git step below does the publishing, so a
        # missing XWORLDCUPTWIT_REPO in .env can't send this match to the WC repo.
        steps = [[PY, "-m", "laliga.run_match", "--fotmob-id", raw,
                  "--season", season, "--no-post", "--no-push"]]
        target = f"FotMob id {raw}"

    elif action == "rebuild":
        steps = list(REBUILD_STEPS)

    elif action == "deploy":
        msg = f"[LaLiga] dashboard refresh ({season})"
        steps = [["git", "add", "-A"],
                 ["git", "commit", "-m", msg],
                 ["git", "push"]]

    # Push through the local clone's own remote for every action — one route, and it can't
    # land in the wrong repository the way git_ops' XWORLDCUPTWIT_REPO default can.
    if push and action != "deploy":
        steps = steps + [["git", "add", "-A"],
                         ["git", "commit", "-m", f"[LaLiga] {target} ({season})"],
                         ["git", "push"]]

    return Job(ACTIONS[action], steps, season, target, optional=optional)


# ─────────────────────────── state ───────────────────────────

def season_state() -> list[dict]:
    """Per-season fixture/scrape counts — what the panel shows above the log."""
    out = []
    for path in sorted(SCHED_DIR.glob("SCHEDULE_*.json")):
        season = path.stem.replace("SCHEDULE_", "")
        try:
            matches = json.loads(path.read_text(encoding="utf-8")).get("matches", [])
        except Exception:
            continue
        played = [m for m in matches if m.get("finished")]
        folder = MATCH_DIR / season
        # exclude the scraper's cookie/matchCentre caches (match_*_cache.json)
        scraped = (len([f for f in folder.glob("*.json") if not f.name.startswith("match_")])
                   if folder.is_dir() else 0)
        out.append({"season": season, "fixtures": len(matches),
                    "played": len(played), "scraped": scraped,
                    "pending": max(len(played) - scraped, 0)})
    return out


# ─────────────────────────── HTTP ───────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):
    control = True

    def __init__(self, *args, **kwargs):
        # Serve from the repo root whatever directory the server was launched from.
        kwargs.setdefault("directory", str(ROOT))
        super().__init__(*args, **kwargs)

    # -- helpers ------------------------------------------------------
    def _origin_ok(self) -> str:
        origin = self.headers.get("Origin", "")
        if not origin:
            return ""
        host = origin.split("//")[-1].split(":")[0]
        if host in ("localhost", "127.0.0.1", "[::1]") or origin in SITE_ORIGINS:
            return origin
        extra = [o.strip() for o in os.environ.get("LALIGA_ALLOWED_ORIGINS", "").split(",") if o.strip()]
        return origin if origin in extra else ""

    def _cors(self) -> None:
        origin = self._origin_ok()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Control-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            # Chrome's Private Network Access preflight: an https page calling loopback.
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        return not TOKEN or self.headers.get("X-Control-Token", "") == TOKEN

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or "{}") if length else {}
        except Exception:
            return {}

    # -- routing ------------------------------------------------------
    def do_OPTIONS(self):                       # noqa: N802 (http.server naming)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):                           # noqa: N802
        if self.control and self.path.startswith("/api/"):
            return self._api("GET")
        return super().do_GET()

    def do_POST(self):                          # noqa: N802
        if self.control and self.path.startswith("/api/"):
            return self._api("POST")
        self.send_error(501, "Unsupported method ('POST')")

    def _api(self, method: str) -> None:
        global _current
        route = self.path.split("?", 1)[0].rstrip("/")
        query = self.path.split("?", 1)[1] if "?" in self.path else ""

        if route == "/api/health":              # unauthenticated: just "a server is here"
            return self._json({"ok": True, "project": "XLALIGA", "auth": bool(TOKEN),
                               "root": str(ROOT), "actions": ACTIONS})
        if not self._authed():
            return self._json({"error": "bad or missing X-Control-Token"}, 401)

        if route == "/api/state" and method == "GET":
            return self._json({"seasons": season_state(),
                               "job": _current.state() if _current else None})

        if route == "/api/job" and method == "GET":
            return self._json({"job": _current.state() if _current else None})

        if route == "/api/job/log" and method == "GET":
            if not _current:
                return self._json({"lines": [], "offset": 0, "job": None})
            offset = 0
            for part in query.split("&"):
                if part.startswith("offset="):
                    offset = int(part.split("=", 1)[1] or 0)
            lines, new_offset = _current.tail(offset)
            return self._json({"lines": lines, "offset": new_offset, "job": _current.state()})

        if route == "/api/job/stop" and method == "POST":
            if _current and _current.running:
                _current.stop()
                return self._json({"ok": True})
            return self._json({"ok": False, "error": "nothing running"}, 409)

        if route == "/api/scrape" and method == "POST":
            with _job_lock:
                if _current and _current.running:
                    return self._json({"error": f"'{_current.label}' is still running"}, 409)
                try:
                    job = build_job(self._body())
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                _current = job
            threading.Thread(target=job.run, daemon=True).start()
            return self._json({"ok": True, "job": job.state()})

        if route == "/api/progress":
            try:
                sys.path.insert(0, str(ROOT))
                from laliga import progress_log
            except Exception as exc:
                return self._json({"error": f"progress_log unavailable: {exc}"}, 500)
            if method == "GET":
                return self._json({"text": progress_log.recent(25)})
            body = self._body()
            text = str(body.get("text", "")).strip()
            kind = str(body.get("kind", "platform"))
            if not text:
                return self._json({"error": "empty note"}, 400)
            if kind == "platform":
                ok = progress_log.log_platform(text)
            else:
                ok = progress_log.log_lesson(text, worked=(kind == "worked"))
            return self._json({"ok": bool(ok)}, 200 if ok else 500)

        return self._json({"error": f"no route {route}"}, 404)

    # -- static -------------------------------------------------------
    def end_headers(self):
        # Force UTF-8 charset headers for HTML, CSS, and JS to prevent character decoding errors
        if not self.path.startswith("/api/"):
            ctype = self.guess_type(self.translate_path(self.path))
            if ctype == 'text/html':
                self.send_header('Content-Type', 'text/html; charset=utf-8')
            elif ctype == 'text/css':
                self.send_header('Content-Type', 'text/css; charset=utf-8')
            elif ctype == 'text/javascript':
                self.send_header('Content-Type', 'text/javascript; charset=utf-8')
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/job/log"):     # the poller would drown the console
            return
        super().log_message(fmt, *args)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the La Liga dashboard (+ Scraper control API).")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default=HOST, help="bind address (default loopback only)")
    ap.add_argument("--no-control", action="store_true", help="static files only")
    args = ap.parse_args()

    Handler.control = not args.no_control
    os.chdir(ROOT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with Server((args.host, args.port), Handler) as httpd:
        print(f"Serving La Liga Dashboard on http://localhost:{args.port}/laliga_dashboard/index.html")
        if Handler.control:
            print(f"Scraper control API enabled on http://{args.host}:{args.port}/api/  "
                  f"({'token required' if TOKEN else 'no token'})")
            print("The dashboard's ⚡ Scraper button appears automatically while this is running.")
        else:
            print("Control API disabled (--no-control).")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server.")


if __name__ == '__main__':
    main()
