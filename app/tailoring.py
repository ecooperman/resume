"""Tailoring a resume and writing a cover letter for one job, in the background.

It is as long a run as drafting a resume (the answer is a whole resume and a letter: a minute or two), so, as in drafts.py, the page only
STARTS it (a request that returns at once) and then asks how it is going; that survives a reload or a closed tab. One run at a time per
account (a person's own Score/queue runs are separate), kept in memory like drafts.py: a restart forgets a run in flight, and the page
then simply shows the job without new documents and the button to try again. What it makes is saved on the job (generated_resume_yaml,
generated_cover_letter, resume_generated_at) only when the whole thing worked, so a failed attempt never overwrites a good earlier one."""
import logging
import threading
import time
from typing import Dict, Optional, Tuple

import yaml
from fastapi import HTTPException
from shared_python import owned
from shared_python.accounts_gate import Account

from . import apikeys, clock, config, crud, generate, limits
from .database import SessionLocal
from .scoring import resume_for

log = logging.getLogger(__name__)

KEEP_SECONDS = 3600  # a finished or failed run is forgotten after this long

_lock = threading.Lock()
_runs: Dict[Tuple[int, int], dict] = {}  # (account id, job id) -> {state: running | done | error, error, started_at, finished_at}


def _forget_old(now: float) -> None:
    for key in [k for k, r in _runs.items() if r["state"] != "running" and now - r["finished_at"] > KEEP_SECONDS]:
        del _runs[key]


def status(account_id: int, job_id: int, now: Optional[float] = None) -> dict:
    """{state: idle | running | done | error, elapsed, error} for one job."""
    now = time.time() if now is None else now
    with _lock:
        _forget_old(now)
        run = _runs.get((account_id, job_id))
        if run is None:
            return {"state": "idle"}
        out = {"state": run["state"], "elapsed": int((run.get("finished_at") or now) - run["started_at"])}
        if run["state"] == "error":
            out["error"] = run["error"]
        return out


def clear(account_id: int, job_id: int) -> None:
    """The page has shown how it ended: forget it. A run still going is left alone."""
    with _lock:
        run = _runs.get((account_id, job_id))
        if run is not None and run["state"] != "running":
            del _runs[(account_id, job_id)]


def start(db, account: Account, job) -> dict:
    """Begin tailoring for this job (which the caller has fetched, so it is the account's own). Raises HTTPException with words for the person
    when it cannot begin (no description, no resume, no usable key, one already running, the hourly allowance used up)."""
    if not (job.jd_text or "").strip():
        raise HTTPException(status_code=400, detail="Add this job's description first: the resume is tailored to it.")
    person = resume_for(db, job)
    try:
        resume_data = yaml.safe_load(person.resume_yaml)
    except yaml.YAMLError:
        raise HTTPException(status_code=400, detail="Your resume couldn't be read: fix it on the Resumes page first.")
    choice = apikeys.pick(db, account)
    try:
        limits.start(("tailor", account.id), max_runs=config.TAILORS_PER_HOUR, what="make a tailored resume")
    except limits.TooBusy as e:
        raise HTTPException(status_code=429, detail=str(e))
    title, company, salary, jd, job_id, account_id = job.name, job.company, job.salary, job.jd_text, job.id, account.id
    with _lock:
        _runs[(account_id, job_id)] = {"state": "running", "started_at": time.time(), "error": None}

    def run() -> None:
        used: list = []
        try:
            try:
                tailored, letter = generate.tailor_resume(choice.key, resume_data, title, company, salary, jd, usage=used)
            finally:
                if choice.metered:  # a run on the system key is counted whether or not it worked: it was paid for
                    with SessionLocal() as session:
                        crud.record_house_use(session, account_id, "tailor", used)
            with owned.scope(account_id), SessionLocal() as session:
                saved = crud.get_job(session, job_id)
                if saved is None:
                    raise generate.ResumeGenerationError("That job was deleted while its resume was being made.")
                saved.generated_resume_yaml = yaml.safe_dump(tailored, sort_keys=False, allow_unicode=True)
                saved.generated_cover_letter = letter
                saved.resume_generated_at = clock.utcnow()
                session.commit()
            outcome = {"state": "done"}
        except generate.ResumeGenerationError as e:
            outcome = {"state": "error", "error": str(e)}
        except Exception:  # never show a stack trace or an internal message to the page; the log has it
            log.exception("tailoring job %s failed for account %s", job_id, account_id)
            outcome = {"state": "error", "error": "Something went wrong while making the resume. Try again in a moment."}
        finally:
            limits.finish(("tailor", account_id))
        with _lock:
            _runs[(account_id, job_id)].update(outcome, finished_at=time.time())

    threading.Thread(target=run, name=f"tailor-{account_id}-{job_id}", daemon=True).start()
    return status(account_id, job_id)


def reset() -> None:
    """For tests."""
    with _lock:
        _runs.clear()
