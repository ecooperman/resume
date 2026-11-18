"""The builder's stored material: each person's engagements (client and project work, described with one structured form) and
their notes. Kept in the database, private to their owner (shared_python.owned), so every later draft can use them: when LinkedIn or
the resume changes, the person updates the resume and these come along instead of being typed again.

Every text field is cut to a sensible length and cleaned of control characters; a person can keep MAX_ENGAGEMENTS at most. The
routes are in app/admin.py; this module is the validation and the way the rows become the plain dicts the drafting code reads."""
import json
import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from . import models

MAX_ENGAGEMENTS = 60
MAX_TEXT = 300
MAX_TECHNOLOGIES = 30
MAX_TECHNOLOGY = 60
MAX_OUTCOMES = 10
MAX_NOTES = 5000
REGULATED = ("yes", "no", "unknown")


def _line(value: object, limit: int = MAX_TEXT) -> str:
    """One line of plain text: control characters out, runs of spaces folded. Too long is an error, not a quiet cut."""
    text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))).strip()
    if len(text) > limit:
        raise ValueError(f"That's too long (at most {limit} characters)")
    return text


class EngagementIn(BaseModel):
    employer: str = ""
    client: str
    client_does: str = ""
    industry: str = ""
    regulated: str = "unknown"
    regulators: str = ""
    technologies: List[str] = Field(default_factory=list)
    role: str = ""
    start: str = ""
    end: str = ""
    outcomes: List[str] = Field(default_factory=list)
    confidential: bool = False

    @field_validator("employer", "client", "client_does", "industry", "regulators", "role", "start", "end")
    @classmethod
    def _short_text(cls, value):
        return _line(value)

    @field_validator("client")
    @classmethod
    def _client_needed(cls, value):
        if not value:
            raise ValueError("Say who it was for (the client or project)")
        return value

    @field_validator("regulated")
    @classmethod
    def _regulated(cls, value):
        if value not in REGULATED:
            raise ValueError("Regulated must be yes, no or unknown")
        return value

    @field_validator("technologies")
    @classmethod
    def _technologies(cls, value):
        if len(value) > MAX_TECHNOLOGIES:
            raise ValueError(f"At most {MAX_TECHNOLOGIES} technologies")
        cleaned = [_line(item, MAX_TECHNOLOGY) for item in value]
        return list(dict.fromkeys(item for item in cleaned if item))  # no empties, no repeats, order kept

    @field_validator("outcomes")
    @classmethod
    def _outcomes(cls, value):
        if len(value) > MAX_OUTCOMES:
            raise ValueError(f"At most {MAX_OUTCOMES} outcomes")
        return [item for item in (_line(item) for item in value) if item]


class EngagementOut(BaseModel):
    id: int
    employer: str
    client: str
    client_does: str
    industry: str
    regulated: str
    regulators: str
    technologies: List[str]
    role: str
    start: str
    end: str
    outcomes: List[str]
    confidential: bool
    position: int


class NotesIn(BaseModel):
    notes: str

    @field_validator("notes")
    @classmethod
    def _notes(cls, value):
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value or "").strip()
        if len(value) > MAX_NOTES:
            raise ValueError(f"The notes are too long ({MAX_NOTES} characters at most)")
        return value


def to_out(row: models.Engagement) -> EngagementOut:
    return EngagementOut(
        id=row.id, employer=row.employer, client=row.client, client_does=row.client_does, industry=row.industry,
        regulated=row.regulated, regulators=row.regulators, technologies=json.loads(row.technologies or "[]"), role=row.role,
        start=row.start, end=row.end, outcomes=json.loads(row.outcomes or "[]"), confidential=row.confidential, position=row.position,
    )


def _apply(row: models.Engagement, body: EngagementIn) -> None:
    for field in ("employer", "client", "client_does", "industry", "regulated", "regulators", "role", "start", "end", "confidential"):
        setattr(row, field, getattr(body, field))
    row.regulators = body.regulators if body.regulated == "yes" else ""  # who regulates it only means something when it is regulated
    row.technologies = json.dumps(body.technologies)
    row.outcomes = json.dumps(body.outcomes)


def list_engagements(db: Session) -> List[models.Engagement]:
    return db.query(models.Engagement).order_by(models.Engagement.position, models.Engagement.id).all()  # only the signed-in person's own


def get_engagement(db: Session, engagement_id: int) -> Optional[models.Engagement]:
    return db.query(models.Engagement).filter(models.Engagement.id == engagement_id).first()


def create_engagement(db: Session, body: EngagementIn) -> models.Engagement:
    existing = list_engagements(db)
    if len(existing) >= MAX_ENGAGEMENTS:
        raise OverflowError(f"You can keep up to {MAX_ENGAGEMENTS} engagements. Remove one to add another.")
    row = models.Engagement(position=(existing[-1].position + 1) if existing else 0)
    _apply(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_engagement(db: Session, engagement_id: int, body: EngagementIn) -> Optional[models.Engagement]:
    row = get_engagement(db, engagement_id)
    if row is None:
        return None
    _apply(row, body)
    db.commit()
    db.refresh(row)
    return row


def delete_engagement(db: Session, engagement_id: int) -> bool:
    row = get_engagement(db, engagement_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def get_notes(db: Session) -> str:
    row = db.query(models.BuilderNotes).first()
    return row.notes if row else ""


def save_notes(db: Session, notes: str) -> str:
    row = db.query(models.BuilderNotes).first()
    if row is None:
        row = models.BuilderNotes(notes=notes)
        db.add(row)
    else:
        row.notes = notes
    db.commit()
    return notes


def for_prompt(db: Session) -> List[dict]:
    """The person's engagements as plain dicts for the drafting code (empty fields left out), in their order."""
    out = []
    for row in list_engagements(db):
        data = to_out(row).model_dump(exclude={"id", "position"})
        out.append({key: value for key, value in data.items() if value not in ("", [], "unknown", False) or key == "confidential" and value})
    return out
