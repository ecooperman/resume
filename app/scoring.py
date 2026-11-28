"""Scoring jobs against a person's resume: one job at a time (the Score button), or a whole list in the background.

The background queue exists because scoring takes ~15 seconds a job, so a list of 100 is 25 minutes: far too long for a page to sit open,
and a request that long would be cut off by the proxy in front of the app anyway. A person's queue lives in memory, like drafts.py: a
restart forgets it (the Jobs page then offers "Score the N jobs with no score yet" again, which is cheap to press). The extension's
pushes feed it too, so jobs it sends in get scored without anyone pressing anything.

Each job goes through exactly the same steps as the Score button (score_one): their key, the hourly allowance, one AI run, counted on
the system key where that applies. One account has at most one queue; more jobs pushed while it runs join it."""
import logging
import threading
import time
from typing import Dict, List, Optional

import yaml
from fastapi import HTTPException
from shared_python import owned, user_keys
from shared_python.accounts_gate import Account

from . import apikeys, clock, config, crud, generate, limits, models
from .database import SessionLocal

log = logging.getLogger(__name__)

KEEP_SECONDS = 3600  # a finished queue is forgotten after this long
BUSY_RETRY_SECONDS = 2.0  # waiting for the person's own Score click to finish
BUSY_RETRIES = 30


def resume_for(db, job: models.Job) -> models.Person:
    """Which of the person's resumes a job is scored (and later tailored) from: the one it is tied to, else their default or only one."""
    person = crud.get_person(db, job.person_id) if job.person_id else None
    if person is None:
        person = crud.get_default_person(db)
    if person is None:
        people = crud.list_people(db)
        person = people[0] if people else None
    if person is None:
        raise HTTPException(status_code=400, detail="Save your resume first (Build my resume): a job is scored against it.")
    return person


class JobProblem(HTTPException):
    """This one job could not be scored (the answer was unusable), which says nothing about the next one."""


def blocked_reason(db, account: Account) -> Optional[str]:
    """Why scoring cannot start for this person right now (no resume saved, no usable key), in words for them, or None if it can."""
    if not crud.list_people(db):
        return "Save your resume first (Build my resume): jobs are scored against it."
    try:
        apikeys.pick(db, account)
    except HTTPException as e:
        return str(e.detail)
    return None


def score_one(db, account: Account, job: models.Job) -> models.Job:
    """Score this job against the person's resume and keep the result on it. One AI run: their own key, or the system key if an
    administrator allowed them it (counted). Raises HTTPException with words for the person; the job is untouched unless it all worked."""
    if not (job.jd_text or "").strip():
        raise HTTPException(status_code=400, detail="Paste this job's description first: scoring reads it.")
    person = resume_for(db, job)
    try:
        resume_data = yaml.safe_load(person.resume_yaml)
    except yaml.YAMLError:
        raise HTTPException(status_code=400, detail="Your resume couldn't be read: fix it on the Resumes page first.")
    choice = apikeys.pick(db, account)
    try:
        limits.start(("score", account.id), max_runs=config.SCORES_PER_HOUR, what="score")
    except limits.TooBusy as e:
        raise HTTPException(status_code=429, detail=str(e))
    used: list = []
    try:
        result = generate.score_job(choice.key, resume_data, job.name, job.company, job.jd_text, usage=used)
    except generate.UnusableAnswer as e:
        raise JobProblem(status_code=400, detail=str(e))
    except generate.ResumeGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        limits.finish(("score", account.id))
        if choice.metered:
            crud.record_house_use(db, account.id, "score", used)
    for field in ("tier", "summary", "matched", "gaps", "strengths"):
        setattr(job, field, result[field] or None)
    if result["salary"]:
        job.salary = result["salary"]
    job.scored_at = clock.utcnow()
    db.commit()
    db.refresh(job)
    return job


# --- the background queue ---------------------------------------------------------------------------------------------------------------

_lock = threading.Lock()
_queues: Dict[int, dict] = {}


def _fresh(now: float) -> dict:
    return {"state": "running", "pending": [], "total": 0, "done": 0, "failed": 0, "error": None, "first_failure": None, "stop": False,
            "started_at": now, "finished_at": None}


def _forget_old(now: float) -> None:
    for account_id in [a for a, q in _queues.items() if q["state"] != "running" and now - q["finished_at"] > KEEP_SECONDS]:
        del _queues[account_id]


def status(account_id: int, now: Optional[float] = None) -> dict:
    """What the Jobs page needs: {state: idle | running | done | stopped | error, total, done, failed, remaining, error, first_failure}."""
    now = time.time() if now is None else now
    with _lock:
        _forget_old(now)
        q = _queues.get(account_id)
        if q is None:
            return {"state": "idle"}
        return {"state": q["state"], "total": q["total"], "done": q["done"], "failed": q["failed"], "remaining": len(q["pending"]),
                "error": q["error"], "first_failure": q["first_failure"]}


def stop(account_id: int) -> None:
    """Ask a running queue to stop after the job it is on."""
    with _lock:
        q = _queues.get(account_id)
        if q is not None and q["state"] == "running":
            q["stop"] = True


def clear(account_id: int) -> None:
    """The page has shown how it ended: forget it. A queue still running is left alone."""
    with _lock:
        q = _queues.get(account_id)
        if q is not None and q["state"] != "running":
            del _queues[account_id]


def enqueue(account: Account, job_ids: List[int]) -> int:
    """Queue these jobs for scoring (ones already waiting are not added twice) and start the worker if it is not running. Returns how many
    were added. The caller has checked the jobs are the account's own."""
    now = time.time()
    with _lock:
        q = _queues.get(account.id)
        if q is None or q["state"] != "running":
            q = _queues[account.id] = _fresh(now)
            start_worker = True
        else:
            start_worker = False
        fresh = [i for i in dict.fromkeys(job_ids) if i not in q["pending"]]
        q["pending"].extend(fresh)
        q["total"] += len(fresh)
    if start_worker:
        # The worker has no request, so it is handed the person's session now (the key was fetched during this request, by blocked_reason's
        # check, and is answered from memory) and holds it only until the queue ends.
        session = user_keys.current()
        threading.Thread(target=_work, args=(account, session), name=f"score-{account.id}", daemon=True).start()
    return len(fresh)


def _finish(account_id: int, state: str, error: Optional[str] = None) -> None:
    with _lock:
        q = _queues[account_id]
        q["state"], q["error"], q["finished_at"] = state, error, time.time()


def _one(account: Account, job_id: int) -> Optional[str]:
    """Score one queued job. Returns None if it is done or no longer needs it, else why it could not be (and whether that ends the whole queue is for _work to say)."""
    with SessionLocal() as db:
        job = crud.get_job(db, job_id)
        if job is None or job.tier:  # deleted since, or scored by hand in the meantime
            return "skip"
        score_one(db, account, job)
    return None


def _work(account: Account, session=None) -> None:
    try:
        with user_keys.use(session), owned.scope(account.id):
            while True:
                with _lock:
                    q = _queues[account.id]
                    if q["stop"]:
                        q["pending"].clear()
                        break
                    if not q["pending"]:
                        break
                    job_id = q["pending"][0]
                problem, fatal = _run_one_with_retries(account, job_id)
                with _lock:
                    q = _queues[account.id]
                    q["pending"].remove(job_id)
                    if problem is None:
                        q["done"] += 1
                    elif fatal:
                        pass  # said once, as the reason the queue ended, not again as a job that failed
                    elif problem != "skip":
                        q["failed"] += 1
                        q["first_failure"] = q["first_failure"] or problem
                    if fatal:
                        q["pending"].clear()
                        q["error"] = problem
        with _lock:
            q = _queues[account.id]
            ended_with = q["error"]
        _finish(account.id, "error" if ended_with else ("stopped" if _stopped(account.id) else "done"), ended_with)
    except Exception:  # never show a stack trace to the page; the log has it
        log.exception("score queue failed for account %s", account.id)
        _finish(account.id, "error", "Something went wrong while scoring. Press the button again in a moment.")


def _stopped(account_id: int) -> bool:
    with _lock:
        return _queues[account_id]["stop"]


def _run_one_with_retries(account: Account, job_id: int):
    """(problem, ends_the_queue). A problem that would be the same for every job (no key, no resume, the allowance used up) ends the
    queue with its words; one job's own trouble (an unreadable answer) is counted and the queue goes on."""
    for attempt in range(BUSY_RETRIES):
        try:
            return _one(account, job_id), False
        except HTTPException as e:
            if e.status_code == 429 and "already running" in str(e.detail) and attempt < BUSY_RETRIES - 1:
                time.sleep(BUSY_RETRY_SECONDS)  # the person pressed Score on one job themselves: let it finish
                continue
            return str(e.detail), not isinstance(e, JobProblem)  # anything but this job's own unusable answer would be the same for the next one
        except Exception:
            log.exception("scoring job %s failed", job_id)
            return "Something went wrong scoring a job.", False
    return "Another score was running too long.", False


def reset() -> None:
    """For tests."""
    with _lock:
        _queues.clear()
