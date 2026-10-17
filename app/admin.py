"""
Admin API + static page for managing resume access codes.

This is a deliberately separate FastAPI app/process/port from app/main.py
(the public resume, which is intentionally Cloudflare-Access-Bypassed on its
own hostname). Keeping admin on its own port means Cloudflare's per-hostname
Access policy is a real network-level boundary -- there's no route from the
public hostname to this process at all -- rather than something app code
has to get right on every request. See README.md.

Shares the same access_codes table as app/main.py/manage.py via crud.py.
"""
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from . import config, crud, generate, render
from .deps import get_db

ADMIN_STATIC_DIR = Path(__file__).parent.parent / "admin_static"
SITE_DIR = Path(__file__).parent.parent / "site"

app = FastAPI(title="Resume Admin")


# --- internal-only endpoints, for the time-management app's resume
# generation feature (see time-management's app/resume_gen.py). Not exposed
# through Cloudflare - this port has no tunnel route - but gated by a shared
# secret anyway as defense in depth against any other same-droplet process.
def require_internal_token(x_internal_token: Optional[str] = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_TOKEN")
    if not expected:
        # Fails closed: if the operator hasn't set the token, these routes
        # are unusable rather than silently unauthenticated.
        raise HTTPException(status_code=503, detail="INTERNAL_API_TOKEN is not configured")
    if x_internal_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing internal token")


class RenderRequest(BaseModel):
    data: dict
    format: Literal["pdf", "docx"]


class CoverLetterRenderRequest(BaseModel):
    basics: dict
    cover_letter_text: str
    format: Literal["pdf", "docx"]


@app.get("/api/resume-people", dependencies=[Depends(require_internal_token)])
def list_resume_people(db: Session = Depends(get_db)):
    """Every person available to tailor from - source list for the
    time-management app's per-person resume dropdown (see People in its
    Settings page)."""
    return {"people": [{"slug": p.slug, "name": p.name, "is_default": p.is_default} for p in crud.list_people(db)]}


@app.get("/api/resume-data", dependencies=[Depends(require_internal_token)])
def get_resume_data(person: Optional[str] = None, db: Session = Depends(get_db)):
    """The given person's resume data (by slug), or the default person's if
    person is omitted, as JSON - source data for tailoring a resume to a
    specific job elsewhere. Read fresh from the DB on every call, so an
    edit made through the People admin UI is always picked up immediately."""
    row = crud.get_person_by_slug(db, person) if person else crud.get_default_person(db)
    if row is None:
        detail = f"No such person: {person!r}" if person else "No default person is configured yet"
        raise HTTPException(status_code=404, detail=detail)
    return yaml.safe_load(row.resume_yaml)


@app.post("/api/render", dependencies=[Depends(require_internal_token)])
def render_document(body: RenderRequest):
    """Render arbitrary resume-shaped data (e.g. a job-tailored variant, not
    necessarily this repo's own evan-resume.yaml) into a PDF or DOCX. Always
    writes into a throwaway temp directory - this never touches site/ or
    any resume yaml file, so it can't affect the real resume site."""
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        try:
            if body.format == "pdf":
                render.build_pdf(body.data, out_dir)
                out_path = out_dir / "resume.pdf"
                media_type = "application/pdf"
            else:
                render.build_docx(body.data, out_dir)
                out_path = out_dir / "resume.docx"
                media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except Exception as e:
            # Most likely a malformed/incomplete resume-shaped payload (e.g.
            # missing "basics") surfacing as a Jinja2 UndefinedError or a
            # plain KeyError, rather than an operational failure.
            raise HTTPException(status_code=400, detail=f"Could not render resume data: {e}")
        return Response(content=out_path.read_bytes(), media_type=media_type)


@app.post("/api/render-cover-letter", dependencies=[Depends(require_internal_token)])
def render_cover_letter_document(body: CoverLetterRenderRequest):
    """Render a generated cover letter (basics for the letterhead + the
    plain-text body from time-management's resume-generation feature) into
    a PDF or DOCX. Same throwaway-temp-dir pattern as /api/render - there's
    no cover-letter equivalent of a stored per-person template, this is
    always ad-hoc content passed straight through from the caller."""
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        try:
            if body.format == "pdf":
                render.build_cover_letter_pdf(body.basics, body.cover_letter_text, out_dir)
                out_path = out_dir / "cover_letter.pdf"
                media_type = "application/pdf"
            else:
                render.build_cover_letter_docx(body.basics, body.cover_letter_text, out_dir)
                out_path = out_dir / "cover_letter.docx"
                media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not render cover letter: {e}")
        return Response(content=out_path.read_bytes(), media_type=media_type)


# --- Resume generation: browser-facing, same Cloudflare-Access-only gating
# as the People CRUD below. Takes uploaded source material, returns
# generated YAML text for the admin UI to drop into the "Add person" form
# for review/editing - never writes a Person itself (see app/generate.py's
# docstring for why that review step matters).
class GenerateResumeOut(BaseModel):
    resume_yaml: str


@app.post("/api/generate-resume", response_model=GenerateResumeOut)
async def generate_resume_route(
    linkedin_profile: Optional[UploadFile] = File(default=None),
    documents: list[UploadFile] = File(default=[]),
    notes: str = Form(default=""),
):
    if not config.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured - see README.md for setup")

    try:
        linkedin_text = None
        if linkedin_profile is not None and linkedin_profile.filename:
            linkedin_text = generate.extract_text(linkedin_profile.filename, await linkedin_profile.read())

        doc_texts = []
        for upload in documents:
            if not upload.filename:
                continue
            doc_texts.append((upload.filename, generate.extract_text(upload.filename, await upload.read())))

        resume_yaml = generate.generate_resume_yaml(linkedin_text, doc_texts, notes)
    except generate.ResumeGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return GenerateResumeOut(resume_yaml=resume_yaml)


# --- People: browser-facing CRUD for the admin UI (admin_static/), gated
# only by the Cloudflare Access already covering this hostname - same
# pattern as the access-code endpoints below, no internal token involved.
# This is the only place resume content is ever created or edited; there is
# deliberately no file/upload-to-disk path anywhere in this app any more.


def _rebuild_person_site(person) -> None:
    """Regenerates this one person's site/private/<slug>/ and
    site/public/<slug>/ trees - same layout build.py (the deploy-time CLI
    step) produces for everyone. Called after a Person create/update, since
    editing their resume_yaml is the only thing that can change what their
    slug-keyed tree should show - without this, an admin-UI edit would
    silently sit in the DB and not actually appear on the live site until
    the next git push triggers a real deploy. Switching who's *default*
    doesn't need a rebuild at all under this layout: every person's tree
    already exists at their own slug regardless of default status: only
    which slug app/main.py resolves to for a bare (no ?person=) request
    changes. Best-effort: logs and swallows a rendering failure (e.g. a
    transient Playwright hiccup) rather than losing the Person write that
    already succeeded - the site can always be rebuilt by hand
    (`python build.py`) or by the next deploy either way."""
    try:
        data = yaml.safe_load(person.resume_yaml)
        render.build_tree(data, SITE_DIR / "private" / person.slug)
        render.build_tree(render.redact(data), SITE_DIR / "public" / person.slug)
    except Exception as e:
        print(f"warning: site rebuild for {person.slug!r} failed: {e}")


def _remove_person_site(slug: str) -> None:
    """Deletes a person's site/{private,public}/<slug>/ trees - called
    after a Person delete, so a removed person doesn't leave an orphaned
    (if harmless - no Person row means ?person=<slug> already 404s
    regardless) directory sitting on disk indefinitely."""
    shutil.rmtree(SITE_DIR / "private" / slug, ignore_errors=True)
    shutil.rmtree(SITE_DIR / "public" / slug, ignore_errors=True)


class PersonCreateRequest(BaseModel):
    slug: str
    name: str
    resume_yaml: str
    is_default: bool = False


class PersonUpdateRequest(BaseModel):
    name: Optional[str] = None
    resume_yaml: Optional[str] = None
    is_default: Optional[bool] = None


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
    # resume_yaml deliberately left out of the list shape - could be a few
    # KB per person; fetched separately (PersonDetailOut) only when actually
    # editing one.


class PersonDetailOut(PersonOut):
    resume_yaml: str


@app.get("/api/people", response_model=list[PersonOut])
def list_people_route(db: Session = Depends(get_db)):
    return crud.list_people(db)


@app.get("/api/people/{person_id}", response_model=PersonDetailOut)
def get_person_route(person_id: int, db: Session = Depends(get_db)):
    person = crud.get_person(db, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


@app.post("/api/people", response_model=PersonDetailOut)
def create_person_route(body: PersonCreateRequest, db: Session = Depends(get_db)):
    try:
        person = crud.create_person(db, body.slug, body.name, body.resume_yaml, body.is_default)
    except crud.PersonError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _rebuild_person_site(person)
    return person


@app.patch("/api/people/{person_id}", response_model=PersonDetailOut)
def update_person_route(person_id: int, body: PersonUpdateRequest, db: Session = Depends(get_db)):
    try:
        person = crud.update_person(
            db, person_id, name=body.name, resume_yaml=body.resume_yaml, is_default=body.is_default
        )
    except crud.PersonError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    _rebuild_person_site(person)
    return person


@app.delete("/api/people/{person_id}")
def delete_person_route(person_id: int, db: Session = Depends(get_db)):
    person = crud.get_person(db, person_id)
    result = crud.delete_person(db, person_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Person not found")
    if result == "is_default":
        raise HTTPException(
            status_code=409, detail="Can't delete the default person - make someone else default first"
        )
    if person is not None:
        _remove_person_site(person.slug)
    return {"ok": True}


class CreateCodeRequest(BaseModel):
    label: Optional[str] = None
    days: Optional[int] = None
    # Who this code unlocks private info for (see AccessCode's docstring on
    # why codes are person-scoped now). None defaults to the current
    # default person, matching the pre-multi-person behavior for the
    # common case of "I only ever generate codes for myself".
    person_id: Optional[int] = None


class CodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    label: Optional[str]
    person_id: Optional[int]
    person_name: Optional[str] = None
    person_slug: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    last_used_at: Optional[datetime]
    use_count: int
    status: str = "active"


def _status(access_code) -> str:
    if access_code.revoked_at is not None:
        return "revoked"
    if access_code.expires_at is not None and access_code.expires_at < datetime.utcnow():
        return "expired"
    return "active"


def _to_out(access_code, db: Session) -> CodeOut:
    out = CodeOut.model_validate(access_code)
    out.status = _status(access_code)
    if access_code.person_id is not None:
        person = crud.get_person(db, access_code.person_id)
        if person is not None:
            out.person_name = person.name
            out.person_slug = person.slug
    return out


@app.get("/api/codes", response_model=list[CodeOut])
def list_codes(db: Session = Depends(get_db)):
    return [_to_out(c, db) for c in crud.list_codes(db)]


@app.post("/api/codes", response_model=CodeOut)
def create_code(body: CreateCodeRequest, db: Session = Depends(get_db)):
    person_id = body.person_id
    if person_id is None:
        default_person = crud.get_default_person(db)
        person_id = default_person.id if default_person else None
    access_code = crud.generate_code(db, label=body.label, days_valid=body.days, person_id=person_id)
    return _to_out(access_code, db)


@app.post("/api/codes/{code_id}/revoke", response_model=CodeOut)
def revoke_code_route(code_id: int, db: Session = Depends(get_db)):
    access_code = crud.revoke_code(db, str(code_id))
    if access_code is None:
        raise HTTPException(status_code=404, detail="Code not found or already revoked")
    return _to_out(access_code, db)


app.mount("/", StaticFiles(directory=ADMIN_STATIC_DIR, html=True), name="static")
