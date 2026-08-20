import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

import yaml
from sqlalchemy.orm import Session

from . import models


class PersonError(Exception):
    """A bad slug, unparseable YAML, or a rule violation (e.g. deleting the
    only/default person) - always safe to show directly in an API error."""


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise PersonError(
            f"Invalid slug {slug!r} - lowercase letters, numbers, and hyphens only, "
            "starting with a letter or number."
        )


def _validate_yaml(resume_yaml: str) -> None:
    try:
        data = yaml.safe_load(resume_yaml)
    except yaml.YAMLError as e:
        raise PersonError(f"Not valid YAML: {e}")
    if not isinstance(data, dict) or "basics" not in data:
        raise PersonError('Resume YAML must be a mapping with at least a top-level "basics" key.')


def list_people(db: Session) -> list[models.Person]:
    return db.query(models.Person).order_by(models.Person.name).all()


def get_person(db: Session, person_id: int) -> Optional[models.Person]:
    return db.query(models.Person).filter(models.Person.id == person_id).first()


def get_person_by_slug(db: Session, slug: str) -> Optional[models.Person]:
    return db.query(models.Person).filter(models.Person.slug == slug).first()


def get_default_person(db: Session) -> Optional[models.Person]:
    return db.query(models.Person).filter(models.Person.is_default.is_(True)).first()


def _clear_other_defaults(db: Session, except_id: Optional[int] = None) -> None:
    q = db.query(models.Person).filter(models.Person.is_default.is_(True))
    if except_id is not None:
        q = q.filter(models.Person.id != except_id)
    q.update({models.Person.is_default: False})


def create_person(db: Session, slug: str, name: str, resume_yaml: str, is_default: bool = False) -> models.Person:
    _validate_slug(slug)
    _validate_yaml(resume_yaml)
    if get_person_by_slug(db, slug) is not None:
        raise PersonError(f"A person with slug {slug!r} already exists.")
    # The very first person created is always the default, regardless of
    # what was passed - there must never be zero default people.
    is_default = is_default or list_people(db) == []
    if is_default:
        _clear_other_defaults(db)
    person = models.Person(slug=slug, name=name, resume_yaml=resume_yaml, is_default=is_default)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def update_person(
    db: Session,
    person_id: int,
    *,
    name: Optional[str] = None,
    resume_yaml: Optional[str] = None,
    is_default: Optional[bool] = None,
) -> Optional[models.Person]:
    person = get_person(db, person_id)
    if person is None:
        return None
    if name is not None:
        person.name = name
    if resume_yaml is not None:
        _validate_yaml(resume_yaml)
        person.resume_yaml = resume_yaml
    if is_default is True:
        _clear_other_defaults(db, except_id=person.id)
        person.is_default = True
    elif is_default is False:
        if person.is_default and len(list_people(db)) > 1:
            raise PersonError(
                "Can't unset the default person without making someone else the default first."
            )
        person.is_default = False
    db.commit()
    db.refresh(person)
    return person


def delete_person(db: Session, person_id: int) -> str:
    """Returns "ok", "not_found", or "is_default" (refuse - there must
    always be a default person; make someone else default first)."""
    person = get_person(db, person_id)
    if person is None:
        return "not_found"
    if person.is_default:
        return "is_default"
    db.delete(person)
    db.commit()
    return "ok"


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
