"""Jobs: what a person is considering, with a fit score and a resume and cover letter tailored to each. Everything here belongs to the signed-in
person and no one else can see it (shared_python.owned filters every query below the routes, so a job id that is not yours is simply a 404).

Jobs arrive two ways: pasted into the page one at a time, or imported from the job-scoring Chrome extension's CSV (many at once, matched by link,
so re-importing a list refreshes scores and never touches what you tracked). Scoring and tailoring are in the sections below this one."""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import config, crud, models
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
