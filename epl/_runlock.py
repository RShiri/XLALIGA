"""
Cross-process scrape lock — serialises EPL scrapers so only ONE runs at a time.

Why this exists
---------------
The WhoScored scrape launches undetected-chromedriver, whose patcher renames a
freshly-downloaded chromedriver into a SHARED path
(``%APPDATA%\\undetected_chromedriver\\undetected_chromedriver.exe``). If two
scrapers start at the same moment they race on that rename and one dies with
``[WinError 183] Cannot create a file when that file already exists`` while the
other's Chrome session wedges (120 s read-timeout) and yields zero events —
exactly what silently dropped Morocco-Haiti, South Africa-South Korea and
Paraguay-Australia.

A per-match schedule stagger is NOT sufficient: the Task Scheduler tasks use
``-StartWhenAvailable -WakeToRun``, so several triggers missed while the PC was
asleep all fire together the instant it wakes, defeating any stagger.

This module takes an OS-level exclusive lock on a single lock file, so a second
scraper simply waits for the first to finish regardless of *when* it was
triggered. The lock is advisory at the process level and is released
automatically when the holding process exits (even on crash), so it can never
deadlock the pipeline permanently.
"""

from __future__ import annotations

import os
import time
import logging
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("epl.runlock")

# One lock file shared by every scraper on this machine. %TEMP% is per-user and
# always writable; fall back to the module directory if it is somehow unset.
_LOCK_DIR = Path(os.environ.get("TEMP") or os.environ.get("TMP") or Path(__file__).parent)
_LOCK_PATH = _LOCK_DIR / "epl_scrape.lock"


@contextmanager
def scrape_lock(timeout: float = 1800.0, poll: float = 5.0):
    """
    Block until an exclusive lock is held, then release it on exit.

    Args:
        timeout: max seconds to wait for the lock before giving up and
                 proceeding anyway (so a stuck holder can never block forever).
        poll:    seconds between acquisition attempts while waiting.

    Yields:
        True if the lock was acquired, False if we proceeded after timing out.
    """
    deadline = time.time() + timeout
    fh = open(_LOCK_PATH, "a+")
    acquired = False
    waited = False
    try:
        while True:
            try:
                _acquire(fh)
                acquired = True
                break
            except OSError:
                if time.time() >= deadline:
                    log.warning(
                        "scrape_lock: timed out after %.0fs waiting for another "
                        "scraper; proceeding without the lock.", timeout)
                    break
                if not waited:
                    log.info("scrape_lock: another scraper is running — waiting…")
                    waited = True
                time.sleep(poll)
        yield acquired
    finally:
        if acquired:
            _release(fh)
        try:
            fh.close()
        except OSError:
            pass


def _acquire(fh) -> None:
    """Non-blocking exclusive lock on the first byte; raises OSError if held."""
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(fh) -> None:
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
