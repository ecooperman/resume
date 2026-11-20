"""Jobs: what a person is considering, with a fit score and a resume and cover letter tailored to each. Everything here belongs to the signed-in
person and no one else can see it (shared_python.owned filters every query below the routes, so a job id that is not yours is simply a 404).

Jobs arrive two ways: pasted into the page one at a time, or imported from the job-scoring Chrome extension's CSV (many at once, matched by link,
so re-importing a list refreshes scores and never touches what you tracked). Scoring and tailoring are in the sections below this one."""
import csv
import io
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from shared_python import accounts_gate
from shared_python.accounts_gate import Account
from sqlalchemy.orm import Session

from . import config, crud, models, render, scoring, tailoring
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


@router.get("/{job_id:int}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    return _detail(_get(db, job_id))


@router.patch("/{job_id:int}")
def update_job(job_id: int, body: JobPatch, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    try:
        return _detail(crud.update_job(db, job, body.model_dump(exclude_unset=True)))
    except crud.JobError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{job_id:int}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    crud.delete_job(db, _get(db, job_id))
    return {"ok": True}


# --- scoring: how well this job fits the person's resume -------------------------------------------------------------------------------


@router.post("/{job_id:int}/score")
def score_job(job_id: int, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """Score this job against the person's resume (fit tier, what matches, the gaps, their strengths, the pay) and keep the result on the job.
    One AI run, on their own key, or the system key if an administrator allowed them it (counted)."""
    return _detail(scoring.score_one(db, account, _get(db, job_id)))


@router.get("/score-queue/status")
def score_queue_status(account: Account = Depends(accounts_gate.current_account)):
    """How the background scoring is going: {state: idle | running | done | stopped | error, total, done, failed, remaining, error}."""
    return scoring.status(account.id)


@router.post("/score-queue")
def start_score_queue(account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """Score, in the background, every job that has a description and no score yet. Returns at once; the page asks how it is going."""
    ids = [j.id for j in crud.list_jobs(db) if j.jd_text and not j.tier]
    if not ids:
        raise HTTPException(status_code=400, detail="Every job with a description already has a score.")
    scoring.enqueue(account, ids)
    return scoring.status(account.id)


@router.delete("/score-queue")
def stop_score_queue(account: Account = Depends(accounts_gate.current_account), clear: bool = False):
    """Stop the background scoring after the job it is on (`?clear=true`: instead forget how a finished one ended)."""
    (scoring.clear if clear else scoring.stop)(account.id)
    return scoring.status(account.id)


# --- tailoring: a resume and cover letter made for this job (app/tailoring.py runs it in the background) -------------------------------------


@router.post("/{job_id:int}/tailor")
def start_tailoring(job_id: int, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """START making a resume and cover letter for this job from the person's resume (one AI run on their own key, or the system key if an
    administrator allowed them it, counted). Returns at once; the page asks `/tailor` how it is going and then reads the job."""
    return tailoring.start(db, account, _get(db, job_id))


@router.get("/{job_id:int}/tailor")
def tailoring_status(job_id: int, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """{state: idle | running | done | error, elapsed, error} for this job. Someone else's job is a 404, like everywhere here."""
    _get(db, job_id)
    return tailoring.status(account.id, job_id)


@router.delete("/{job_id:int}/tailor")
def clear_tailoring(job_id: int, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """The page has shown how it ended: forget it. A run still going is left alone."""
    _get(db, job_id)
    tailoring.clear(account.id, job_id)
    return tailoring.status(account.id, job_id)


_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _filename(job: models.Job, ext: str, kind: str) -> str:
    base = f"{job.name} - {job.company}" if job.company else job.name
    base = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 _.-]", " ", f"{base} - {kind}")).strip()
    return f"{base or f'{kind}_{job.id}'}.{ext}"


def _file(job: models.Job, body: bytes, fmt: str, kind: str) -> Response:
    media = "application/pdf" if fmt == "pdf" else _DOCX
    return Response(content=body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{_filename(job, fmt, kind)}"', "Cache-Control": "private, no-store"})


@router.get("/{job_id:int}/resume")
def download_tailored_resume(job_id: int, format: Literal["pdf", "docx"] = "pdf", db: Session = Depends(get_db)):
    """The resume made for this job, as a PDF or Word file, rendered on the spot (nothing is stored but its text)."""
    job = _get(db, job_id)
    if not job.generated_resume_yaml:
        raise HTTPException(status_code=404, detail="No resume has been made for this job yet")
    try:
        data = yaml.safe_load(job.generated_resume_yaml)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            if format == "pdf":
                render.build_pdf(data, out_dir)
                body = (out_dir / "resume.pdf").read_bytes()
            else:
                render.build_docx(data, out_dir)
                body = (out_dir / "resume.docx").read_bytes()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not render this resume: {e}")
    return _file(job, body, format, "Resume")


@router.get("/{job_id:int}/cover-letter")
def download_cover_letter(job_id: int, format: Literal["pdf", "docx"] = "pdf", db: Session = Depends(get_db)):
    """The cover letter made for this job as a PDF or Word file (the letterhead is the tailored resume's contact details)."""
    job = _get(db, job_id)
    if not job.generated_cover_letter:
        raise HTTPException(status_code=404, detail="No cover letter has been made for this job yet")
    try:
        basics = (yaml.safe_load(job.generated_resume_yaml) or {}).get("basics", {}) if job.generated_resume_yaml else {}
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            if format == "pdf":
                render.build_cover_letter_pdf(basics, job.generated_cover_letter, out_dir)
                body = (out_dir / "cover_letter.pdf").read_bytes()
            else:
                render.build_cover_letter_docx(basics, job.generated_cover_letter, out_dir)
                body = (out_dir / "cover_letter.docx").read_bytes()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not render this cover letter: {e}")
    return _file(job, body, format, "Cover Letter")
