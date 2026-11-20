"""One-time move of a person's jobs out of time-management's database (its `jobs` table) into this app (`manage.py import-jobs`).

Read-only on the old database. Nothing here overwrites what the person already has in this app (some of the jobs may have come over earlier by
the extension's CSV): a job whose link is already here only has its GAPS filled in (a missing score, description, resume label, tailored resume
and letter, creation date), and `applied` is kept if EITHER side had it. A job's label (time-management's "person" tag) becomes the resume it is
tailored from, through the `resume_person_slug` set on the label there; a label with none, or whose slug is not one of this person's resumes
here, leaves the job untied (their default resume), and is reported."""
import sqlite3
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from . import config, crud, models

TEXT_LIMITS = {"name": 300, "company": 300, "company_url": 2000, "url": 2000, "salary": 300, "tier": 40, "summary": 5000, "matched": 8000, "gaps": 8000,
               "strengths": 8000, "jd_text": config.MAX_JD_CHARS}
FILL = ("company", "company_url", "salary", "tier", "summary", "matched", "gaps", "strengths", "scored_at", "jd_text", "generated_resume_yaml",
        "generated_cover_letter", "resume_generated_at")  # filled in on a job already here ONLY if it has nothing there; never replaced


class LegacyImportError(Exception):
    """Said in words for the person at the keyboard."""


def _when(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _read(path: str, owner_email: str):
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # read-only: this can never change the old database
        source.row_factory = sqlite3.Row
        user = source.execute("select id from users where lower(email) = ?", (crud.normalize_email(owner_email),)).fetchone()
        if user is None:
            raise LegacyImportError(f"{owner_email} has no account in {path}.")
        labels = {r["id"]: r["resume_person_slug"] for r in source.execute("select id, resume_person_slug from people where owner_id = ?", (user["id"],))}
        jobs = [dict(r) for r in source.execute("select * from jobs where owner_id = ? order by id", (user["id"],))]
        source.close()
    except sqlite3.Error as e:
        raise LegacyImportError(f"Could not read {path}: {e}")
    return labels, jobs


def import_jobs(db: Session, path: str, owner_email: str, dry_run: bool = False) -> Dict[str, object]:
    """Move one account's jobs across. The caller has put `db` in that owner's scope (owned.scope). Returns the counts and anything to look at:
    {created, merged, skipped, labels_unmatched, over_limit, dry_run}. With dry_run nothing is kept."""
    labels, jobs = _read(path, owner_email)
    slug_to_id = {p.slug: p.id for p in crud.list_people(db)}
    created = merged = skipped = over_limit = 0
    unmatched = set()
    room = config.MAX_JOBS_PER_ACCOUNT - crud.job_count(db)
    for old in jobs:
        values = {k: (str(old[k]).strip()[:limit] if old.get(k) is not None and str(old[k]).strip() else None) for k, limit in TEXT_LIMITS.items()}
        values.update(scored_at=_when(old.get("scored_at")), resume_generated_at=_when(old.get("resume_generated_at")),
                      generated_resume_yaml=(old.get("generated_resume_yaml") or None), generated_cover_letter=(old.get("generated_cover_letter") or None))
        if not values["url"] or not values["name"]:
            skipped += 1
            continue
        slug = labels.get(old.get("person_id"))
        person_id = slug_to_id.get(slug) if slug else None
        if slug and person_id is None:
            unmatched.add(slug)
        existing = db.query(models.Job).filter(models.Job.url == values["url"]).first()
        if existing is not None:
            for field in FILL:
                if values[field] is not None and getattr(existing, field) is None:
                    setattr(existing, field, values[field])
            if person_id is not None and existing.person_id is None:
                existing.person_id = person_id
            existing.applied = bool(existing.applied or old.get("applied"))
            merged += 1
            continue
        if room <= 0:
            over_limit += 1
            continue
        db.add(models.Job(applied=bool(old.get("applied")), person_id=person_id, created_at=_when(old.get("created_at")) or datetime.utcnow(),
                          **{k: v for k, v in values.items()}))
        room -= 1
        created += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"created": created, "merged": merged, "skipped": skipped, "over_limit": over_limit, "labels_unmatched": sorted(unmatched), "dry_run": dry_run}
