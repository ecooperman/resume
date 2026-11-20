import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

import yaml
from shared_python import owned
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from . import config, models
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
    db.query(models.Job).filter(models.Job.person_id == person_id).update({"person_id": None}, synchronize_session=False)  # their jobs stay, tailored from the default
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


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(email: str) -> bool:
    """An address that looks like one (already normalised); never the placeholder that holds the pre-accounts data."""
    return bool(_EMAIL.match(email)) and email != LEGACY_OWNER_EMAIL


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


# --- jobs: what a person is considering, scored and tailored (app/jobs.py). Owned: every query below sees only the signed-in person's.


class JobError(Exception):
    """Something wrong with a job request that is safe to show as it is."""


def list_jobs(db: Session) -> list[models.Job]:
    return db.query(models.Job).order_by(models.Job.id.desc()).all()


def get_job(db: Session, job_id: int) -> Optional[models.Job]:
    return db.query(models.Job).filter(models.Job.id == job_id).first()


def job_count(db: Session) -> int:
    return db.query(func.count(models.Job.id)).scalar()


def _check_person(db: Session, person_id: Optional[int]) -> None:
    if person_id is not None and get_person(db, person_id) is None:
        raise JobError("That resume isn't one of yours")


def _clean(value, limit: int) -> Optional[str]:
    text = (value or "").strip() if isinstance(value, str) else value
    return text[:limit] if text else None


def create_job(db: Session, data: dict) -> models.Job:
    if job_count(db) >= config.MAX_JOBS_PER_ACCOUNT:
        raise JobError(f"You can keep up to {config.MAX_JOBS_PER_ACCOUNT} jobs. Delete some you are done with first.")
    name = _clean(data.get("name"), 300)
    if not name:
        raise JobError("A job needs a title")
    url = _clean(data.get("url"), 2000)
    if url and db.query(models.Job).filter(models.Job.url == url).first() is not None:
        raise JobError("You already have a job with that link")
    _check_person(db, data.get("person_id"))
    job = models.Job(
        name=name, company=_clean(data.get("company"), 300), company_url=_clean(data.get("company_url"), 2000), url=url,
        notes=_clean(data.get("notes"), 5000), salary=_clean(data.get("salary"), 300), jd_text=_clean(data.get("jd_text"), config.MAX_JD_CHARS),
        person_id=data.get("person_id"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


_EDITABLE = {"name": 300, "company": 300, "company_url": 2000, "url": 2000, "notes": 5000, "salary": 300, "jd_text": config.MAX_JD_CHARS}


def update_job(db: Session, job: models.Job, changes: dict) -> models.Job:
    for field, limit in _EDITABLE.items():
        if field in changes:
            value = _clean(changes[field], limit)
            if field == "name" and not value:
                raise JobError("A job needs a title")
            if field == "url" and value and value != job.url and db.query(models.Job).filter(models.Job.url == value).first() is not None:
                raise JobError("You already have a job with that link")
            setattr(job, field, value)
    if "applied" in changes and changes["applied"] is not None:
        job.applied = bool(changes["applied"])
    if "person_id" in changes:
        _check_person(db, changes["person_id"])
        job.person_id = changes["person_id"]
    db.commit()
    db.refresh(job)
    return job


def delete_job(db: Session, job: models.Job) -> None:
    db.delete(job)
    db.commit()


def import_jobs_from_csv(db: Session, rows: list, person_id: Optional[int] = None) -> dict:
    """Bulk-upsert jobs from a scored-jobs CSV (the job-scoring Chrome extension's export: url, title, company, salary, tier, summary, matched,
    gaps, strengths, scored_at, jd_text). A job already here (matched by url) has ONLY its score fields refreshed, so re-scoring a list never
    touches `applied`, notes or the resume made for it. The resume label is set on new jobs, and filled in on old ones that have none, but
    never changed on one that already has it. Rows without a url, or new ones without a title, are skipped."""
    _check_person(db, person_id)
    created = updated = skipped = 0
    room = config.MAX_JOBS_PER_ACCOUNT - job_count(db)
    for row in rows:
        url = _clean(row.get("url"), 2000)
        if not url:
            skipped += 1
            continue
        scored_at = None
        raw = (row.get("scored_at") or "").strip()
        if raw:
            try:
                scored_at = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                scored_at = None
        score = {
            "salary": _clean(row.get("salary"), 300), "tier": _clean(row.get("tier"), 40), "summary": _clean(row.get("summary"), 5000),
            "matched": _clean(row.get("matched"), 8000), "gaps": _clean(row.get("gaps"), 8000), "strengths": _clean(row.get("strengths"), 8000),
            "scored_at": scored_at, "jd_text": _clean(row.get("jd_text"), config.MAX_JD_CHARS),
        }
        existing = db.query(models.Job).filter(models.Job.url == url).first()
        if existing is not None:
            for field, value in score.items():
                setattr(existing, field, value)
            if person_id is not None and existing.person_id is None:
                existing.person_id = person_id
            updated += 1
            continue
        title = _clean(row.get("title"), 300)
        if not title or room <= 0:
            skipped += 1
            continue
        db.add(models.Job(name=title, url=url, company=_clean(row.get("company"), 300), person_id=person_id, **score))
        room -= 1
        created += 1
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


def push_jobs(db: Session, items: list, person_id: Optional[int] = None) -> dict:
    """Add postings the Chrome extension captured. Like the CSV import, a posting already here (same link) is matched by url, but a push may
    carry no score at all, so it is gentler: an existing job keeps its score, notes and 'applied' mark, and only gets the description,
    pay or company it was missing. A posting that DOES come with a score (tier) refreshes the score fields as the import does. Returns the
    counts and `to_score`, the ids of jobs now holding a description but no score, for the caller to queue."""
    _check_person(db, person_id)
    created = updated = skipped = 0
    room = config.MAX_JOBS_PER_ACCOUNT - job_count(db)
    touched = []
    for item in items:
        url = _clean(item.get("url"), 2000)
        if not url:
            skipped += 1
            continue
        title, company, salary = _clean(item.get("title"), 300), _clean(item.get("company"), 300), _clean(item.get("salary"), 300)
        jd_text = _clean(item.get("jd_text"), config.MAX_JD_CHARS)
        tier = _clean(item.get("tier"), 40)
        existing = db.query(models.Job).filter(models.Job.url == url).first()
        if existing is None:
            if not title or room <= 0:
                skipped += 1
                continue
            existing = models.Job(name=title, url=url, company=company, salary=salary, jd_text=jd_text, person_id=person_id)
            db.add(existing)
            room -= 1
            created += 1
        else:
            existing.company = existing.company or company
            existing.salary = existing.salary or salary
            existing.jd_text = existing.jd_text or jd_text
            if person_id is not None and existing.person_id is None:
                existing.person_id = person_id
            updated += 1
        if tier:
            existing.tier = tier
            for field, limit in (("summary", 5000), ("matched", 8000), ("gaps", 8000), ("strengths", 8000)):
                setattr(existing, field, _clean(item.get(field), limit))
            existing.scored_at = datetime.utcnow()
            if salary:
                existing.salary = salary
        touched.append(existing)
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "to_score": [j.id for j in touched if j.jd_text and not j.tier]}


# --- personal tokens for the Chrome extension (app/push.py)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def list_tokens(db: Session) -> list[models.PushToken]:
    return db.query(models.PushToken).order_by(models.PushToken.id.desc()).all()


def create_token(db: Session, name: str) -> tuple[models.PushToken, str]:
    """Make a token for the signed-in person. Returns the row and the token itself, which exists only in this return value: only its hash is kept."""
    name = _clean(name, 80)
    if not name:
        raise JobError("Give the token a name, such as the computer it is for")
    if db.query(func.count(models.PushToken.id)).scalar() >= config.MAX_TOKENS_PER_ACCOUNT:
        raise JobError(f"You can have up to {config.MAX_TOKENS_PER_ACCOUNT} tokens. Remove one you no longer use first.")
    raw = config.PUSH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = models.PushToken(name=name, prefix=raw[: len(config.PUSH_TOKEN_PREFIX) + 4], token_hash=hash_token(raw))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def delete_token(db: Session, token_id: int) -> bool:
    row = db.query(models.PushToken).filter(models.PushToken.id == token_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def user_for_token(db: Session, raw: str) -> Optional[models.User]:
    """Whose token this is, or None. Must run inside owned.unscoped() (it is the lookup that decides who the scope is). Notes when it was
    last used, at most once a minute so a burst of pushes is not a burst of writes."""
    if not raw or not raw.startswith(config.PUSH_TOKEN_PREFIX):
        return None
    row = db.query(models.PushToken).filter(models.PushToken.token_hash == hash_token(raw)).first()
    if row is None:
        return None
    now = datetime.utcnow()
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        row.last_used_at = now
        db.commit()
    return db.query(models.User).filter(models.User.id == row.owner_id).first()


# --- the server's Anthropic key: who may use it, and what they have used (app/apikeys.py)


def _month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def set_house_key(db: Session, user, allowed: bool):
    user.house_key = allowed
    db.commit()
    return user


def house_uses_this_month(db: Session, user_id: int, now: Optional[datetime] = None) -> int:
    return db.query(func.count(models.HouseKeyUse.id)).filter(models.HouseKeyUse.user_id == user_id, models.HouseKeyUse.at >= _month_start(now)).scalar()


def record_house_use(db: Session, user_id: int, kind: str, tokens: Optional[list] = None) -> None:
    """Count one run on the server's key. `tokens` is the list of (input, output) pairs the calls made (a draft can make two); None
    or empty when no answer came back to read them from."""
    pairs = [t for t in (tokens or []) if t]
    db.add(models.HouseKeyUse(user_id=user_id, kind=kind, input_tokens=sum(i for i, _ in pairs) if pairs else None, output_tokens=sum(o for _, o in pairs) if pairs else None))
    db.commit()


def house_usage_by_user(db: Session, now: Optional[datetime] = None) -> dict:
    """{user_id: {runs, input_tokens, output_tokens}} for this calendar month, for the Users page."""
    rows = (
        db.query(models.HouseKeyUse.user_id, func.count(models.HouseKeyUse.id), func.coalesce(func.sum(models.HouseKeyUse.input_tokens), 0), func.coalesce(func.sum(models.HouseKeyUse.output_tokens), 0))
        .filter(models.HouseKeyUse.at >= _month_start(now))
        .group_by(models.HouseKeyUse.user_id)
        .all()
    )
    return {uid: {"runs": runs, "input_tokens": int(i), "output_tokens": int(o)} for uid, runs, i, o in rows}


def estimated_cost(input_tokens: int, output_tokens: int) -> float:
    return round((input_tokens * config.PRICE_INPUT_PER_MTOK + output_tokens * config.PRICE_OUTPUT_PER_MTOK) / 1_000_000, 2)


# --- invites: asking for someone to be let in. A person asks, an administrator decides (app/invites.py).


def invites_left(db: Session, user_id: int, is_admin: bool) -> Optional[int]:
    """How many more people this person may ask for, or None for no limit (administrators). Each different email they have asked for
    counts once, in any state, until they withdraw a waiting one: being refused does not hand the slot back to be spent again."""
    if is_admin:
        return None
    used = db.query(func.count(models.Invite.id)).filter(models.Invite.invited_by_id == user_id).scalar()
    return max(config.INVITES_PER_USER - used, 0)


def create_invite(db: Session, inviter_id: int, is_admin: bool, email: str):
    """(invite, None), or (None, "duplicate") if this email was ever asked for, or (None, "none_left")."""
    if db.query(models.Invite).filter(models.Invite.email == email).first() is not None:
        return None, "duplicate"
    left = invites_left(db, inviter_id, is_admin)
    if left is not None and left <= 0:
        return None, "none_left"
    invite = models.Invite(email=email, invited_by_id=inviter_id)
    db.add(invite)
    try:
        db.commit()
    except IntegrityError:  # the same request raced in twice
        db.rollback()
        return None, "duplicate"
    db.refresh(invite)
    return invite, None


def get_invite(db: Session, invite_id: int):
    return db.query(models.Invite).options(joinedload(models.Invite.invited_by)).filter(models.Invite.id == invite_id).first()


def my_invites(db: Session, user_id: int):
    return db.query(models.Invite).filter(models.Invite.invited_by_id == user_id).order_by(models.Invite.id).all()


def invites_with_status(db: Session, status: str):
    return db.query(models.Invite).options(joinedload(models.Invite.invited_by)).filter(models.Invite.status == status).order_by(models.Invite.id).all()


def pending_invite_count(db: Session) -> int:
    return db.query(func.count(models.Invite.id)).filter(models.Invite.status == models.INVITE_PENDING).scalar()


def decide_invite(db: Session, invite, status: str):
    invite.status = status
    invite.decided_at = datetime.utcnow()
    db.commit()
    return invite


def remove_invite(db: Session, invite) -> None:
    db.delete(invite)
    db.commit()


def set_user_status(db: Session, user, status: str):
    user.status = status
    db.commit()
    return user


def delete_user_data(db: Session, user_id: int) -> dict:
    """Permanently delete everything a person owns. Runs inside THEIR scope (never anyone else's). Returns the counts and the
    slugs whose built site files the caller should remove."""
    db.query(models.Invite).filter(models.Invite.invited_by_id == user_id).delete(synchronize_session=False)  # not owned: picked out by who asked
    db.query(models.HouseKeyUse).filter(models.HouseKeyUse.user_id == user_id).delete(synchronize_session=False)
    with owned.scope(user_id):
        slugs = [slug for (slug,) in db.query(models.Person.slug).all()]
        db.query(models.Job).delete(synchronize_session=False)  # before the resumes they point at
        db.query(models.PushToken).delete(synchronize_session=False)
        codes = db.query(models.AccessCode).delete(synchronize_session=False)
        people = db.query(models.Person).delete(synchronize_session=False)
        keys = db.query(models.UserApiKey).delete(synchronize_session=False)
        engagements = db.query(models.Engagement).delete(synchronize_session=False)
        db.query(models.BuilderNotes).delete(synchronize_session=False)
        sources = db.query(models.SourceFile).delete(synchronize_session=False)
        db.query(models.BuilderDraft).delete(synchronize_session=False)
        db.commit()
    return {"people": people, "codes": codes, "keys": keys, "engagements": engagements, "sources": sources, "slugs": slugs}
