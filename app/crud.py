import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from . import models


def generate_code(db: Session, label: Optional[str] = None, days_valid: Optional[int] = None) -> models.AccessCode:
    """Create a new access code. days_valid=None means it never expires."""
    expires_at = datetime.utcnow() + timedelta(days=days_valid) if days_valid else None
    access_code = models.AccessCode(
        code=secrets.token_urlsafe(24),
        label=label,
        expires_at=expires_at,
    )
    db.add(access_code)
    db.commit()
    db.refresh(access_code)
    return access_code


def get_valid_code(db: Session, code: str) -> Optional[models.AccessCode]:
    """Return the AccessCode row iff it exists, isn't revoked, and isn't expired."""
    if not code:
        return None
    row = db.query(models.AccessCode).filter(models.AccessCode.code == code).first()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at < datetime.utcnow():
        return None
    return row


def touch_code(db: Session, access_code: models.AccessCode) -> None:
    """Record that a valid code was just used (view or download)."""
    access_code.use_count += 1
    access_code.last_used_at = datetime.utcnow()
    db.commit()


def revoke_code(db: Session, code_or_id: str) -> Optional[models.AccessCode]:
    row = _find(db, code_or_id)
    if row is None or row.revoked_at is not None:
        return None
    row.revoked_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def list_codes(db: Session) -> list[models.AccessCode]:
    return db.query(models.AccessCode).order_by(models.AccessCode.created_at.desc()).all()


def _find(db: Session, code_or_id: str) -> Optional[models.AccessCode]:
    if code_or_id.isdigit():
        row = db.query(models.AccessCode).filter(models.AccessCode.id == int(code_or_id)).first()
        if row is not None:
            return row
    return db.query(models.AccessCode).filter(models.AccessCode.code == code_or_id).first()
