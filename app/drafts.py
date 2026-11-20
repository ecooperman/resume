"""Drafting runs in the background. Writing a resume takes the model a minute or two, and a proxy in front of the app (Cloudflare gives up
on a request after 100 seconds) would cut a single long request off while the server went on to finish and save the draft, leaving the
page with an error and nothing to show. So the page only STARTS a draft (a request that returns at once), then asks every few seconds
how it is going. That also survives a page reload or a closed tab: the job keeps running, and the next visit finds it.

One job per account, kept in memory on purpose (like limits.py): a restart forgets it, and the page says the draft was interrupted.
The finished text waits here until the page has shown it and cleared it (or an hour passes), so someone who keeps no files on the
server (a choice they can make) still gets their draft. Nothing is shared between accounts: every call is keyed by the account."""
import logging
import threading
import time
from typing import Callable, Dict, Optional

from . import generate, limits

log = logging.getLogger(__name__)

KEEP_SECONDS = 3600  # a finished or failed job is forgotten after this long if nobody collects it

_lock = threading.Lock()
_jobs: Dict[int, dict] = {}


def _forget_old(now: float) -> None:
    for account_id in [a for a, j in _jobs.items() if j["state"] != "running" and now - j["finished_at"] > KEEP_SECONDS]:
        del _jobs[account_id]


def status(account_id: int, now: Optional[float] = None) -> dict:
    """What the page needs: {state: idle | running | done | error, elapsed, error, resume_yaml, update_person_id}."""
    now = time.time() if now is None else now
    with _lock:
        _forget_old(now)
        job = _jobs.get(account_id)
        if job is None:
            return {"state": "idle"}
        out = {"state": job["state"], "elapsed": int((job.get("finished_at") or now) - job["started_at"]), "update_person_id": job["update_person_id"]}
        if job["state"] == "done":
            out["resume_yaml"] = job["resume_yaml"]
        if job["state"] == "error":
            out["error"] = job["error"]
        return out


def clear(account_id: int) -> None:
    """The page has shown the result (or error): forget it. A job that is still running is left alone."""
    with _lock:
        job = _jobs.get(account_id)
        if job is not None and job["state"] != "running":
            del _jobs[account_id]


def start(account_id: int, work: Callable[[], str], update_person_id: Optional[int] = None) -> None:
    """Run `work` (it returns the resume YAML, or raises) in a background thread and record how it ends. The caller has already taken
    the person's slot with limits.start(account_id); it is given back here when the work ends, however it ends."""
    with _lock:
        _jobs[account_id] = {"state": "running", "started_at": time.time(), "update_person_id": update_person_id}

    def run() -> None:
        try:
            outcome = {"state": "done", "resume_yaml": work()}
        except generate.ResumeGenerationError as e:
            outcome = {"state": "error", "error": str(e)}
        except Exception:  # never show a stack trace or an internal message to the page; the log has it
            log.exception("drafting failed for account %s", account_id)
            outcome = {"state": "error", "error": "Something went wrong while drafting. Try again in a moment."}
        finally:
            limits.finish(account_id)
        with _lock:
            _jobs[account_id].update(outcome, finished_at=time.time())

    threading.Thread(target=run, name=f"draft-{account_id}", daemon=True).start()


def reset() -> None:
    """For tests."""
    with _lock:
        _jobs.clear()
