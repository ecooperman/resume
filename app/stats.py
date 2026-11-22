"""What the dashboard's stats pages ask this app: for each account, when they were last here and how much they have in the app, as
numbers and dates only (never a title, a line of a resume, a note or a key). Administrators only. The counts run inside
`owned.unscoped()` because every table here is private to its owner: that is the one place everyone's rows are counted together."""
from fastapi import APIRouter, Depends
from shared_python import accounts_gate, owned, usage

from . import models
from .database import SessionLocal

router = APIRouter(prefix="/api/admin/stats", tags=["stats"], dependencies=[Depends(accounts_gate.require_admin)])


@router.get("")
def stats():
    with owned.unscoped(), SessionLocal() as db:
        return usage.build(db, models.User, {
            "resumes": usage.per_user(db, models.Person.owner_id, models.Person.created_at),
            "jobs": usage.per_user(db, models.Job.owner_id, models.Job.created_at),
            "jobs scored": usage.per_user(db, models.Job.owner_id, models.Job.scored_at, models.Job.tier.isnot(None)),
            "jobs applied to": usage.per_user(db, models.Job.owner_id, None, models.Job.applied.is_(True)),
            "tailored resumes": usage.per_user(db, models.Job.owner_id, None, models.Job.generated_resume_yaml.isnot(None)),
            "extension tokens": usage.per_user(db, models.PushToken.owner_id, models.PushToken.created_at),
            "AI runs on the house key": usage.per_user(db, models.HouseKeyUse.user_id, models.HouseKeyUse.at),
        })
