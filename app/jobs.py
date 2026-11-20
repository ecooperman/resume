"""Jobs: what a person is considering, with a fit score and a resume and cover letter tailored to each. Everything here belongs to the signed-in
person and no one else can see it (shared_python.owned filters every query below the routes, so a job id that is not yours is simply a 404).

Jobs arrive two ways: pasted into the page one at a time, or imported from the job-scoring Chrome extension's CSV (many at once, matched by link,
so re-importing a list refreshes scores and never touches what you tracked). Scoring and tailoring are in the sections below this one."""
import csv
import io
from datetime import datetime
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from shared_python import accounts_gate
from shared_python.accounts_gate import Account
from sqlalchemy.orm import Session

from . import apikeys, config, crud, generate, limits, models
from .deps import get_db

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobIn(BaseModel):
    name: str
    company: Optional[str] = None
    company_url: Optional[str] = None
    url: Optional[str] = None
    jd_text: Optional[str] = None
    salary: Optional[str] = None
    notes: Optional[str] = None
    person_id: Optional[int] = None


class JobPatch(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    company_url: Optional[str] = None
    url: Optional[str] = None
    jd_text: Optional[str] = None
    salary: Optional[str] = None
    notes: Optional[str] = None
    applied: Optional[bool] = None
    person_id: Optional[int] = None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None  # stored as naive UTC; the Z tells the browser so


def _item(job: models.Job) -> dict:
    """What the list shows: everything except the long texts (the description and the generated documents), which are fetched when a job is opened."""
    return {
        "id": job.id, "name": job.name, "company": job.company, "company_url": job.company_url, "url": job.url, "applied": job.applied,
        "notes": job.notes, "person_id": job.person_id, "salary": job.salary, "tier": job.tier, "summary": job.summary, "matched": job.matched,
        "gaps": job.gaps, "strengths": job.strengths, "scored_at": _iso(job.scored_at), "created_at": _iso(job.created_at),
        "has_description": bool(job.jd_text), "has_documents": bool(job.generated_resume_yaml), "resume_generated_at": _iso(job.resume_generated_at),
    }


def _detail(job: models.Job) -> dict:
    return {**_item(job), "jd_text": job.jd_text, "generated_cover_letter": job.generated_cover_letter, "generated_resume_yaml": job.generated_resume_yaml}


def _get(db: Session, job_id: int) -> models.Job:
    job = crud.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")  # someone else's is indistinguishable from one that does not exist
    return job


@router.get("")
def list_jobs(db: Session = Depends(get_db)):
    return [_item(j) for j in crud.list_jobs(db)]


@router.post("")
def create_job(body: JobIn, db: Session = Depends(get_db)):
    try:
        return _detail(crud.create_job(db, body.model_dump()))
    except crud.JobError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import")
def import_jobs(file: UploadFile = File(...), person_id: Optional[int] = Form(default=None), db: Session = Depends(get_db)):
    """Import the job-scoring Chrome extension's CSV (needs a `url` column). New jobs are added; ones you already have (same link) only have
    their scores refreshed."""
    raw = file.file.read(config.MAX_IMPORT_BYTES + 1)
    if len(raw) > config.MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail=f"That file is over {config.MAX_IMPORT_BYTES // (1024 * 1024)} MB")
    try:
        text = raw.decode("utf-8-sig")  # -sig drops the byte-order mark Excel and Sheets add
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="That doesn't look like a CSV text file")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "url" not in reader.fieldnames:
        raise HTTPException(status_code=400, detail="The CSV needs a 'url' column (it should be the file the job-scoring extension exports)")
    rows = []
    for row in reader:
        rows.append(row)
        if len(rows) > config.MAX_IMPORT_ROWS:
            raise HTTPException(status_code=400, detail=f"That file has more than {config.MAX_IMPORT_ROWS} jobs; import it in parts")
    try:
        return crud.import_jobs_from_csv(db, rows, person_id)
    except crud.JobError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    return _detail(_get(db, job_id))


@router.patch("/{job_id}")
def update_job(job_id: int, body: JobPatch, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    try:
        return _detail(crud.update_job(db, job, body.model_dump(exclude_unset=True)))
    except crud.JobError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    crud.delete_job(db, _get(db, job_id))
    return {"ok": True}


# --- scoring: how well this job fits the person's resume -------------------------------------------------------------------------------


def resume_for(db: Session, job: models.Job) -> models.Person:
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


@router.post("/{job_id}/score")
def score_job(job_id: int, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """Score this job against the person's resume (fit tier, what matches, the gaps, their strengths, the pay) and keep the result on the job.
    One AI run, on their own key, or the system key if an administrator allowed them it (counted)."""
    job = _get(db, job_id)
    if not (job.jd_text or "").strip():
        raise HTTPException(status_code=400, detail="Paste this job's description first: scoring reads it.")
    person = resume_for(db, job)
    try:
        resume_data = yaml.safe_load(person.resume_yaml)
    except yaml.YAMLError:
        raise HTTPException(status_code=400, detail="Your resume couldn't be read: fix it on the People page first.")
    choice = apikeys.pick(db, account)
    try:
        limits.start(("score", account.id), max_runs=config.SCORES_PER_HOUR, what="score")
    except limits.TooBusy as e:
        raise HTTPException(status_code=429, detail=str(e))
    used: list = []
    try:
        result = generate.score_job(choice.key, resume_data, job.name, job.company, job.jd_text, usage=used)
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
    job.scored_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return _detail(job)
