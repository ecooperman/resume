"""Keeps drafting fair: parsing and drafting take real time on this server, so one person can only draft a few times an hour, and
only one draft at a time. (Their own Anthropic key pays for the AI part; this protects the server and the page.) Kept in memory
on purpose: a restart simply forgets, which is harmless for a limit this small."""
import threading
import time
from typing import Dict, Hashable, List

MAX_DRAFTS = 6
WINDOW_SECONDS = 3600

_lock = threading.Lock()
_recent: Dict[Hashable, List[float]] = {}
_running: set = set()


class TooBusy(Exception):
    """Say so in words the person can act on."""


def start(person_id: Hashable, now: float = None) -> None:
    """Take a draft slot for this person (or for any other key, such as ("import", id), which then has its own count), or raise TooBusy. Call finish() when it is over, whether it worked or not."""
    now = time.time() if now is None else now
    with _lock:
        if person_id in _running:
            raise TooBusy("A draft is already running for you. Wait for it to finish.")
        recent = [t for t in _recent.get(person_id, []) if now - t < WINDOW_SECONDS]
        if len(recent) >= MAX_DRAFTS:
            wait = int((recent[0] + WINDOW_SECONDS - now) / 60) + 1
            raise TooBusy(f"You can draft {MAX_DRAFTS} times an hour. Try again in about {wait} minute{'s' if wait != 1 else ''}.")
        recent.append(now)
        _recent[person_id] = recent
        _running.add(person_id)


def finish(person_id: Hashable) -> None:
    with _lock:
        _running.discard(person_id)


def reset() -> None:
    """For tests."""
    with _lock:
        _recent.clear()
        _running.clear()
