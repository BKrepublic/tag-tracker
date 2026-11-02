import threading
import time
import traceback

from . import db, finder
from .config import GOOGLE_SECRETS_PATH, POLL_MINUTES

status = {"last_run": None, "last_error": None, "running": False, "next_run": None}
_wake = threading.Event()


def poll_once() -> int:
    """Fetches every tag once. Returns the number of new locations stored."""
    total = 0
    if not GOOGLE_SECRETS_PATH.exists() or not finder.is_connected():
        # Without this the library would try to open Chrome for sign-in on every poll.
        status["last_error"] = "Google account not connected yet (run the Google sign-in step)"
        status["last_run"] = int(time.time())
        return 0
    status["running"] = True
    try:
        trackers = finder.list_trackers()
        if not trackers:
            status["last_error"] = (
                "Google returned no SPOT tags. Run 'python -m server.manage devices' "
                "to inspect the device types."
            )
            print(f"[poller] {status['last_error']}")
            return 0
        for name, canonic_id in trackers:
            db.upsert_device(canonic_id, name)
            try:
                locs = finder.locate(canonic_id)
                added = db.insert_locations(canonic_id, locs)
                total += added
                db.mark_polled(canonic_id, None)
                print(f"[poller] {name}: {len(locs)} reports, {added} new")
            except Exception as e:
                db.mark_polled(canonic_id, str(e))
                print(f"[poller] {name}: {e}")
        status["last_error"] = None
    except Exception as e:
        status["last_error"] = str(e)
        traceback.print_exc()
    finally:
        status["running"] = False
        status["last_run"] = int(time.time())
    return total


def _loop():
    while True:
        poll_once()
        status["next_run"] = int(time.time() + POLL_MINUTES * 60)
        _wake.wait(POLL_MINUTES * 60)
        _wake.clear()


def start():
    threading.Thread(target=_loop, name="poller", daemon=True).start()


def trigger_now():
    _wake.set()
