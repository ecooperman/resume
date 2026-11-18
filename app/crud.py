import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

import yaml
from shared_python import owned
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from .database import SessionLocal


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


def slug_taken(slug: str) -> bool:
    """Is this slug in use by ANYONE? Slugs are one namespace for the whole site (they are the public ?person= address), so this
    looks past the per-person filter, in a session of its own so nothing unscoped ever mixes with a request's own session."""
    with owned.unscoped(), SessionLocal() as other:
        return other.query(models.Person.id).filter(models.Person.slug == slug).first() is not None


def private_slug(owner_id: int) -> str:
    """A slug for someone who does not choose one: unguessable, and never shown anywhere while their resume is not published."""
    while True:
        slug = f"u{owner_id}-{secrets.token_hex(5)}"
        if not slug_taken(slug):
            return slug


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


def create_person(
    db: Session, slug: str, name: str, resume_yaml: str, is_default: bool = False, allow_default: bool = True
) -> models.Person:
    """A new resume for whoever is in scope (the owner is filled in below the app, see shared_python.owned). `allow_default`
    is False for ordinary accounts: only an administrator's resume can ever be the site's default."""
    _validate_slug(slug)
    _validate_yaml(resume_yaml)
    if slug_taken(slug):
        raise PersonError(f"A person with slug {slug!r} already exists.")
    # An administrator's very first person is always their default - there must never be zero default people.
    is_default = allow_default and (is_default or list_people(db) == [])
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
    published: Optional[bool] = None,
) -> Optional[models.Person]:
    person = get_person(db, person_id)
    if person is None:
        return None
    if name is not None:
        person.name = name
    if resume_yaml is not None:
        _validate_yaml(resume_yaml)
        person.resume_yaml = resume_yaml
    if published is not None:
        person.published = published
        if not published:
            person.is_default = False  # the default must be public: it is what the site's front page shows
    if is_default is True:
        _clear_other_defaults(db, except_id=person.id)
        person.is_default = True
        person.published = True
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


# --- the public site (app/main.py): no one is signed in, so these look past the per-person filter on purpose ---------------------
# A resume is only ever shown here if an administrator has PUBLISHED it. This is the one place the owner filter is bypassed for
# a request that carries no account, so what it may return is spelled out and tested.


def public_person_by_slug(db: Session, slug: str) -> Optional[models.Person]:
    with owned.unscoped():
        return db.query(models.Person).filter(models.Person.slug == slug, models.Person.published.is_(True)).first()


def public_default_person(db: Session) -> Optional[models.Person]:
    with owned.unscoped():
        return db.query(models.Person).filter(models.Person.is_default.is_(True), models.Person.published.is_(True)).first()


def published_people(db: Session) -> list[models.Person]:
    """Everyone whose pages belong on the public site (what build.py builds)."""
    with owned.unscoped():
        return db.query(models.Person).filter(models.Person.published.is_(True)).order_by(models.Person.name).all()


def public_person_by_id(db: Session, person_id: int) -> Optional[models.Person]:
    with owned.unscoped():
        return db.query(models.Person).filter(models.Person.id == person_id, models.Person.published.is_(True)).first()


def generate_code(
    db: Session, label: Optional[str] = None, days_valid: Optional[int] = None, person_id: Optional[int] = None
) -> models.AccessCode:
    """Create a new access code, scoped to person_id (see AccessCode's
    docstring for why this matters). days_valid=None means it never
    expires. person_id=None only makes sense when no person exists yet at
    all (matches build.py's own bootstrapping tolerance) - callers should
    otherwise always pass one, defaulting to the current default person
    when the caller (admin UI, manage.py) doesn't ask for someone specific."""
    expires_at = datetime.utcnow() + timedelta(days=days_valid) if days_valid else None
    access_code = models.AccessCode(
        code=secrets.token_urlsafe(24),
        label=label,
        person_id=person_id,
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
    with owned.unscoped():  # the public site checks codes with nobody signed in; the code itself is the credential
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
    with owned.unscoped():
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


# --- accounts ----------------------------------------------------------------------------------------------------------------

LEGACY_OWNER_EMAIL = "legacy-owner@resume.invalid"


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(func.lower(models.User.email) == normalize_email(email)).first()


def get_user(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()


def list_users(db: Session):
    return db.query(models.User).filter(models.User.email != LEGACY_OWNER_EMAIL).order_by(func.lower(models.User.email)).all()


def adopt_or_create_admin(db: Session, email: str):
    """An administrator's account, made the first time they sign in. If the resumes that existed before accounts are still
    parked on the placeholder, they take them over by giving it their email: it all becomes theirs without a single row moving."""
    email = normalize_email(email)
    existing = get_user_by_email(db, email)
    if existing is not None:
        return existing
    legacy = db.query(models.User).filter(models.User.email == LEGACY_OWNER_EMAIL).first()
    if legacy is not None:
        legacy.email = email
        user = legacy
    else:
        user = models.User(email=email)
        db.add(user)
    db.commit()
    return user


def add_user(db: Session, email: str):
    """Let someone in (an administrator's decision). Returns (user, changed): a person whose access was removed is restored,
    and someone who already has access is left as they are."""
    email = normalize_email(email)
    user = get_user_by_email(db, email)
    if user is not None:
        restored = user.status != models.USER_ACTIVE
        user.status = models.USER_ACTIVE
        db.commit()
        return user, restored
    user = models.User(email=email)
    db.add(user)
    db.commit()
    return user, True


def set_user_status(db: Session, user, status: str):
    user.status = status
    db.commit()
    return user


def delete_user_data(db: Session, user_id: int) -> dict:
    """Permanently delete everything a person owns. Runs inside THEIR scope (never anyone else's). Returns the counts and the
    slugs whose built site files the caller should remove."""
    with owned.scope(user_id):
        slugs = [slug for (slug,) in db.query(models.Person.slug).all()]
        codes = db.query(models.AccessCode).delete(synchronize_session=False)
        people = db.query(models.Person).delete(synchronize_session=False)
        keys = db.query(models.UserApiKey).delete(synchronize_session=False)
        engagements = db.query(models.Engagement).delete(synchronize_session=False)
        db.query(models.BuilderNotes).delete(synchronize_session=False)
        db.commit()
    return {"people": people, "codes": codes, "keys": keys, "engagements": engagements, "slugs": slugs}
