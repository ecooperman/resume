from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from .database import Base


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
