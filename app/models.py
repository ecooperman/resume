from datetime import datetime

from shared_python import owned
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from .database import Base

USER_ACTIVE = "active"
USER_REMOVED = "removed"

INVITE_PENDING = "pending"
INVITE_APPROVED = "approved"
INVITE_DECLINED = "declined"


class User(Base):
    """Someone with an account in the resume app. An administrator lets them in on the Users page (default-deny: signing in
    is not enough) or, for an administrator, the account is made on their first sign-in (taking over the resumes that existed
    before accounts). Everything below belongs to one User (owned.OwnedMixin) and stays private to them."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default=USER_ACTIVE, server_default=USER_ACTIVE)
    created_at = Column(DateTime, default=datetime.utcnow)
    # An administrator's decision: this person may use the SERVER's Anthropic key (up to config.HOUSE_KEY_CALLS_PER_MONTH runs a month)
    # when they have not saved a key of their own. Off by default: nobody else ever uses it.
    house_key = Column(Boolean, nullable=False, default=False, server_default="0")


class HouseKeyUse(Base):
    """One AI run (a draft, a pasted-list import, a review, a score or a tailored resume) that a person made on the server's key: counted against their monthly
    allowance, and kept with the tokens it used so the administrator can see what it really cost. Never holds any text, only counts.
    NOT owned (shared_python.owned): the administrator reads everyone's totals; a person's own are picked out by user_id."""

    __tablename__ = "house_key_uses"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)  # draft | import | review | score | tailor
    at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)


class Invite(Base):
    """A person's request to let someone who has no account yet into the app. An administrator approves it (the person gets an
    account and an email) or declines it. One request per email, ever: a declined one stays on file (and counts against the asker)
    until an administrator allows it again, which removes it. NOT owned (shared_python.owned): the administrator reads everyone's
    in the queue, and each person's own are picked out by invited_by_id in crud."""

    __tablename__ = "invites"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True)  # lowercased
    invited_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default=INVITE_PENDING, server_default=INVITE_PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)

    invited_by = relationship("User")


class Person(Base, owned.OwnedMixin):
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
    # Only a published person can be seen on the public site (app/main.py): anyone's resume is private to its owner
    # unless an administrator has published it. Everything that existed before accounts was already public, so the
    # migration publishes it; every new person starts unpublished.
    published = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Job(Base, owned.OwnedMixin):
    """A job someone is considering: pasted in, or imported from the job-scoring Chrome extension's CSV. Private to its owner (owned). The
    score fields (tier .. strengths) come from the extension's export or from scoring here; `generated_*` are the tailored resume (YAML,
    the same shape as a resume) and cover letter made for this job, rendered to PDF or Word on demand rather than stored as files.
    `person_id` is which of the owner's resumes the job is tailored from (null: their only, or default, resume). One row per URL per
    owner, so re-importing a list refreshes the scores and never touches what they tracked."""

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("owner_id", "url", name="uq_jobs_owner_id_url"),)

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)  # the job title
    company = Column(String, nullable=True)
    company_url = Column(String, nullable=True)
    url = Column(String, nullable=True)  # a pasted job may have none
    applied = Column(Boolean, nullable=False, default=False, server_default="0")
    notes = Column(Text, nullable=True)
    person_id = Column(Integer, ForeignKey("people.id"), nullable=True)
    salary = Column(String, nullable=True)
    tier = Column(String, nullable=True)  # Strong | Good | Possible | Weak | Unclear, or null: not scored yet
    summary = Column(Text, nullable=True)
    matched = Column(Text, nullable=True)
    gaps = Column(Text, nullable=True)
    strengths = Column(Text, nullable=True)
    scored_at = Column(DateTime, nullable=True)
    jd_text = Column(Text, nullable=True)
    generated_resume_yaml = Column(Text, nullable=True)
    generated_cover_letter = Column(Text, nullable=True)
    resume_generated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PushToken(Base, owned.OwnedMixin):
    """A personal token the job-scoring Chrome extension sends to push postings into its owner's Jobs (app/push.py). Made by the signed-in
    person on the Jobs page, shown to them ONCE, and only its SHA-256 kept (the token is long and random, so a plain hash is enough: a
    stolen database does not hand out working tokens). Deleting the row revokes it. `prefix` is just enough to tell one from another."""

    __tablename__ = "push_tokens"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)  # what the person calls it, e.g. "Laptop Chrome"
    prefix = Column(String, nullable=False)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    extension_version = Column(String, nullable=True)  # what the extension last said its version was, so the Jobs page can say an update is out


class AccessCode(Base, owned.OwnedMixin):
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


class UserApiKey(Base, owned.OwnedMixin):
    """A person's own key for a paid service (today only Anthropic, which drafts a resume from their documents). Each person
    brings their own, so nobody is metered on the owner's bill; saved encrypted (shared_python.keystore) and never sent back to
    the browser - only the last four characters are kept in the clear. Private to its owner like everything else here."""

    __tablename__ = "user_api_keys"
    __table_args__ = (UniqueConstraint("owner_id", "provider", name="uq_user_api_keys_owner_provider"),)

    id = Column(Integer, primary_key=True)
    provider = Column(String, nullable=False)
    encrypted_key = Column(Text, nullable=False)
    last4 = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Engagement(Base, owned.OwnedMixin):
    """One piece of client or project work a person describes in the builder: who it was for, what they do, the industry,
    whether it was regulated, what was used, the person's role and what came of it. Kept here (not just inside a drafted
    resume) so every later draft can use it: when LinkedIn or the resume changes, the person updates the resume and these come
    along, instead of being typed again. They are never printed on the resume; they ride in resume.yaml as an optional
    `engagements:` list under the matching job, where a later step can use them to tailor the resume to a job."""

    __tablename__ = "engagements"

    id = Column(Integer, primary_key=True)
    employer = Column(String, nullable=False, default="", server_default="")  # which job it belongs to (the company, as on the resume)
    client = Column(String, nullable=False)
    client_does = Column(String, nullable=False, default="", server_default="")
    industry = Column(String, nullable=False, default="", server_default="")
    regulated = Column(String, nullable=False, default="unknown", server_default="unknown")  # "yes", "no" or "unknown"
    regulators = Column(String, nullable=False, default="", server_default="")  # who regulates it (HIPAA, SOX...), when regulated
    technologies = Column(Text, nullable=False, default="[]", server_default="[]")  # a JSON list of names
    role = Column(String, nullable=False, default="", server_default="")
    start = Column(String, nullable=False, default="", server_default="")
    end = Column(String, nullable=False, default="", server_default="")
    outcomes = Column(Text, nullable=False, default="[]", server_default="[]")  # a JSON list of short statements
    confidential = Column(Boolean, nullable=False, default=False, server_default="0")  # do not name the client on anything printed
    position = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class BuilderNotes(Base, owned.OwnedMixin):
    """The free-text notes a person gives the builder ("leave out my first job", "I want product roles"), kept so a later update
    uses them too. One row per person."""

    __tablename__ = "builder_notes"
    __table_args__ = (UniqueConstraint("owner_id", name="uq_builder_notes_owner"),)

    id = Column(Integer, primary_key=True)
    notes = Column(Text, nullable=False, default="", server_default="")
    # Their choice (on by default) of whether the builder keeps the text of their files and their unsaved draft. Off means nothing from
    # a file is stored: it is held in their browser for the draft and a refresh starts them over.
    keep_files = Column(Boolean, nullable=False, default=True, server_default="1")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SourceFile(Base, owned.OwnedMixin):
    """One thing a person gave the builder: LinkedIn's data export or an old resume. Only its extracted TEXT is kept (for a LinkedIn
    ZIP, just the career files; never the original file, never connections or messages), so a later update starts from what they gave
    before instead of asking again. `kind` is "linkedin" (a newer one replaces an older one) or "document"."""

    __tablename__ = "source_files"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="document", server_default="document")
    text = Column(Text, nullable=False, default="", server_default="")
    found = Column(Text, nullable=False, default="{}", server_default="{}")  # JSON: {"Positions": 3, ...} for LinkedIn data
    summary = Column(String, nullable=False, default="", server_default="")
    size_bytes = Column(Integer, nullable=False, default=0, server_default="0")  # of the original upload, for display
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class BuilderDraft(Base, owned.OwnedMixin):
    """The draft a person is reviewing and has not saved yet, so a reload (or coming back tomorrow) does not lose it. One per person.
    `person_id` is the resume an update merges into, or null for a new one."""

    __tablename__ = "builder_drafts"
    __table_args__ = (UniqueConstraint("owner_id", name="uq_builder_drafts_owner"),)

    id = Column(Integer, primary_key=True)
    resume_yaml = Column(Text, nullable=False, default="", server_default="")
    person_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
