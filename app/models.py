from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

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
    """

    __tablename__ = "access_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False, index=True)
    label = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    use_count = Column(Integer, default=0, nullable=False)
