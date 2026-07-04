#!/usr/bin/env python3
"""One command to (re)build the whole web dashboard.

    py epl_dashboard/build_site.py            # build everything once
    py epl_dashboard/build_site.py --watch    # rebuild whenever a match JSON changes
    py epl_dashboard/build_site.py --serve     # build once, then serve on :8777
    py epl_dashboard/build_site.py --watch --serve

Rebuilds every per-match detail file (matches_detail/*.js) and the index data.js.
The renderer already rebuilds individual games automatically; this is the manual /
bulk entry point and a lightweight watcher for when you are editing or backfilling.
"""
import os
import sys
import time
import glob
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_match_details
import build_data
import build_players
import build_database

MATCH_DIR = os.path.join(ROOT, "epl", "matches")


def build_once():
    t0 = time.time()
    build_match_details.main()
    build_data.main()
    build_players.main()
    build_database.main()
    print(f"Site rebuilt in {time.time() - t0:.1f}s")


def _snapshot():
    snap = {}
    for f in glob.glob(os.path.join(MATCH_DIR, "*.json")):
        try:
            snap[f] = os.path.getmtime(f)
        except OSError:
            pass
    return snap


def watch(interval=3):
    print(f"Watching {MATCH_DIR} for changes (Ctrl+C to stop)…")
    last = _snapshot()
    try:
        while True:
            time.sleep(interval)
            now = _snapshot()
            if now != last:
                changed = [os.path.basename(f) for f in now
                           if last.get(f) != now.get(f)]
                print(f"Change detected ({', '.join(changed[:5])}…) — rebuilding")
                build_once()
                last = now
    except KeyboardInterrupt:
        print("\nStopped watching.")


def serve():
    import http.server
    os.chdir(ROOT)
    port = 8777

    class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
        # Always serve the freshest data.js / detail files after a rebuild.
        def end_headers(self):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

        def log_message(self, *args):
            pass

    # ThreadingHTTPServer so the browser's keep-alive connections don't block.
    httpd = http.server.ThreadingHTTPServer(("", port), NoCacheHandler)
    url = f"http://localhost:{port}/epl_dashboard/index.html"
    print(f"Serving the dashboard at {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        httpd.shutdown()


def main():
    ap = argparse.ArgumentParser(description="Build / watch / serve the WC2026 web dashboard")
    ap.add_argument("--watch", action="store_true", help="rebuild on match-file changes")
    ap.add_argument("--serve", action="store_true", help="serve the site on :8777")
    args = ap.parse_args()

    build_once()

    if args.watch and args.serve:
        import threading
        threading.Thread(target=watch, daemon=True).start()
        serve()
    elif args.watch:
        watch()
    elif args.serve:
        serve()


if __name__ == "__main__":
    main()
