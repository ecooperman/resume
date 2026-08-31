from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from .database import Base


class Person(Base):
    """One resume, uploaded/edited entirely through the admin UI - never a
    file in this repo, never in git. `resume_yaml` is the raw YAML text
    (same shape render.py expects), stored directly in access.db (already
    gitignored, same as the access-codes table). `slug` is the stable,
    human-readable identifier used everywhere external to this table - the
    `?person=` querystring param, and what time-management stores on its
    own Person row - so a numeric id is never exposed outside this app.
    Exactly one row has is_default=True at a time (see crud.upsert_person);
    that's who the public resume site and a bare /api/resume-data (no
    ?person=) resolve to."""

    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    resume_yaml = Column(Text, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AccessCode(Base):
    """A capability token that unlocks the full resume (contact info +
    unredacted downloads) when passed as ?code=... on any route.

    Validity is computed at read time (see crud.get_valid_code), not stored
    as a status column -- a code is valid iff revoked_at is null and
    (expires_at is null or expires_at is in the future).

    person_id scopes a code to exactly one Person - app/main.py looks up
    the code *first* and derives who it's for directly from person_id, so
    there's no separate ?person=<slug> to also get right for the private-
    unlock path (a code is just authoritative on its own); ?person= only
    matters for public browsing with no code at all. Nullable only for rows
    that predate this column; the migration that added it backfills every
    existing code to whichever person was the default at migration time, so
    in practice every code has always really been "for" a specific person -
    this column just makes that explicit instead of implicitly assuming
    "the default person, whoever that currently is" (which would let a code
    silently start unlocking a *different* person's private info the
    moment someone else becomes the new default - a real leak, not just an
    edge case, given this is a household resume site).
    """

    __tablename__ = "access_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False, index=True)
    label = Column(String, nullable=True)
    person_id = Column(Integer, ForeignKey("people.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    use_count = Column(Integer, default=0, nullable=False)
