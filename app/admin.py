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
import difflib
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from shared_python import accounts_gate, oidc_login, owned
from shared_python.accounts_gate import Account
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from . import access, apikeys, builder, config, crud, drafts, feedback, generate, invites, jobs, limits, push, render, tokens, users
from .database import SessionLocal
from .deps import get_db

ADMIN_STATIC_DIR = Path(__file__).parent.parent / "admin_static"
ADMIN_TEMPLATES_DIR = Path(__file__).parent.parent / "admin_templates"
SITE_DIR = config.SITE_DIR

app = FastAPI(title="Resume Admin")
access.install(app)  # only people with an account get in, each inside their own private data (app/access.py)
app.add_middleware(push.PushDoor)  # added after the gate, so outermost: /api/push/... takes a personal token instead (app/push.py)
app.include_router(users.router)
app.include_router(invites.router)
app.include_router(jobs.router)
app.include_router(tokens.router)
app.include_router(invites.app_router)
templates = Jinja2Templates(directory=str(ADMIN_TEMPLATES_DIR))


@app.get("/api/me")
def get_me(request: Request, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """Who is signed in, for the page header and for hiding what only an administrator may use. `invites_left` is how many more people
    they may ask for (null = no limit), and an administrator also gets how many requests are waiting, for the sidebar badge."""
    me = {"email": account.email, "is_admin": account.is_admin, "logout_url": oidc_login.logout_url(request),
          "invites_left": crud.invites_left(db, account.id, account.is_admin)}
    if account.is_admin:
        me["pending_invites"] = crud.pending_invite_count(db)
    return me


def asset_v(name: str) -> str:
    """A version for one of this app's own scripts or stylesheets: its last-modified time. Put on the URL (`/build.js?v=...`) so a
    browser fetches the new file after a deploy instead of running a stale cached one next to the fresh page."""
    try:
        return str(int((ADMIN_STATIC_DIR / name).stat().st_mtime))
    except OSError:
        return "0"


# The two admin pages are templates so the shared-assets URL is baked in server-side (see
# config.SHARED_ASSETS_BASE). Registered before the static mount at the bottom, which serves
# their app.js/style.css and so on.
def _page(name: str, admin_only: bool = False):
    def route(request: Request, account: Account = Depends(accounts_gate.current_account)):
        if admin_only and not account.is_admin:
            return RedirectResponse("/", status_code=302)  # not for them: back to their own resume
        return templates.TemplateResponse(request, name, {"shared_assets_base": config.SHARED_ASSETS_BASE, "asset_v": asset_v})

    return route


app.get("/", response_class=HTMLResponse)(_page("people.html"))
app.get("/build.html", response_class=HTMLResponse)(_page("build.html"))
app.get("/codes.html", response_class=HTMLResponse)(_page("codes.html", admin_only=True))
app.get("/users.html", response_class=HTMLResponse)(_page("users.html", admin_only=True))
app.get("/invites.html", response_class=HTMLResponse)(_page("invites.html"))
app.get("/jobs.html", response_class=HTMLResponse)(_page("jobs.html"))


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


# --- The builder: browser-facing. Takes what a person uploads, drafts a resume.yaml for the page to show for review, and
# never writes a Person itself (see app/generate.py for why that review step matters). It spends the PERSON'S OWN Anthropic key
# (an administrator may fall back to the server's), what they upload is read in memory and only its text is kept (never the original file), and each person is limited to a
# few drafts an hour (app/limits.py).


class ApiKeyIn(BaseModel):
    key: str


class ApiKeySaved(BaseModel):
    status: dict
    verified: Optional[bool]
    note: str = ""


@app.get("/api/api-key")
def api_key_status(account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    return apikeys.status(db, account)


@app.put("/api/api-key", response_model=ApiKeySaved)
def save_api_key(body: ApiKeyIn, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """Check the key with Anthropic, then store it encrypted. A key Anthropic refuses is not saved; one that could not be
    checked (Anthropic was unreachable) is saved and reported as unverified."""
    if not apikeys.storage_available():
        raise HTTPException(status_code=503, detail="Saving keys isn't set up on the server yet - ask the administrator")
    try:
        key = apikeys.clean(body.key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    verified, why = apikeys.verify(key)
    if verified is False:
        raise HTTPException(status_code=400, detail=why)
    apikeys.save(db, key)
    return ApiKeySaved(status=apikeys.status(db, account), verified=verified, note="" if verified else f"Saved, but not verified: {why}.")


@app.delete("/api/api-key")
def remove_api_key(account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    if not apikeys.remove(db):
        raise HTTPException(status_code=404, detail="You haven't saved a key")
    return apikeys.status(db, account)


def _read_uploads(uploads: list[UploadFile]) -> list[tuple[str, bytes]]:
    """The named files, each read up to one byte past the limit (so an oversize one is refused, not silently cut)."""
    return [(Path(u.filename).name, u.file.read(generate.MAX_FILE_BYTES + 1)) for u in uploads if u.filename]


@app.get("/api/builder/sources", response_model=list[builder.SourceOut])
def list_sources(db: Session = Depends(get_db)):
    return [builder.source_out(row) for row in builder.list_sources(db)]


@app.post("/api/builder/inspect")
def inspect_uploads(sources: list[UploadFile] = File(default=[])):
    """What we make of each file, with nothing kept (for someone who chose not to keep files): the same reading as adding them."""
    try:
        files = _read_uploads(sources)
        generate.check_files(files)
    except generate.ResumeGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out = []
    for name, content in files:
        try:
            read = generate.read_source(name, content)
            out.append({"name": name, "ok": True, "kind": read["kind"], "found": read["found"], "summary": read["summary"]})
        except generate.ResumeGenerationError as e:
            out.append({"name": name, "ok": False, "found": {}, "summary": str(e)})
    return {"files": out}


@app.post("/api/builder/sources")
def add_sources(sources: list[UploadFile] = File(default=[]), db: Session = Depends(get_db)):
    """Read each uploaded file (no AI and no key involved) and keep its text for this person, so a later draft or update starts from
    it. The original file is not kept. A file that cannot be read is reported and not kept; the others still are."""
    _must_keep(db)
    try:
        files = _read_uploads(sources)
        generate.check_files(files)
    except generate.ResumeGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    kept, rejected = [], []
    for name, content in files:
        try:
            read = generate.read_source(name, content)
        except generate.ResumeGenerationError as e:
            rejected.append({"name": name, "problem": str(e)})
            continue
        try:
            kept.append(builder.source_out(builder.add_source(db, name, len(content), read)))
        except OverflowError as e:
            rejected.append({"name": name, "problem": str(e)})
    return {"added": kept, "rejected": rejected, "sources": [builder.source_out(row) for row in builder.list_sources(db)]}


@app.delete("/api/builder/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    if not builder.delete_source(db, source_id):
        raise HTTPException(status_code=404, detail="File not found")
    return {"ok": True}


class DraftIn(BaseModel):
    resume_yaml: str = Field(max_length=builder.MAX_DRAFT_CHARS)
    person_id: Optional[int] = None


def _draft_out(row) -> Optional[dict]:
    return None if row is None else {"resume_yaml": row.resume_yaml, "person_id": row.person_id, "updated_at": row.updated_at.isoformat() + "Z"}


@app.get("/api/builder/draft")
def get_draft(db: Session = Depends(get_db)):
    """The draft this person was reviewing and had not saved (or null), so a reload does not lose it."""
    return {"draft": _draft_out(builder.get_draft(db))}


@app.put("/api/builder/draft")
def put_draft(body: DraftIn, db: Session = Depends(get_db)):
    _must_keep(db)
    if body.person_id is not None and crud.get_person(db, body.person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return {"draft": _draft_out(builder.save_draft(db, body.resume_yaml, body.person_id))}


@app.delete("/api/builder/draft")
def delete_draft(db: Session = Depends(get_db)):
    builder.delete_draft(db)
    return {"draft": None}


# --- The material the builder keeps for each person: engagements (client and project work, one structured form) and notes. Private
# to their owner, and used by every later draft, so updating a resume from new LinkedIn data does not mean typing them again.


@app.get("/api/engagements", response_model=list[builder.EngagementOut])
def list_engagements(db: Session = Depends(get_db)):
    return [builder.to_out(row) for row in builder.list_engagements(db)]


@app.post("/api/engagements", response_model=builder.EngagementOut)
def create_engagement(body: builder.EngagementIn, db: Session = Depends(get_db)):
    try:
        return builder.to_out(builder.create_engagement(db, body))
    except OverflowError as e:
        raise HTTPException(status_code=409, detail=str(e))


class ImportIn(BaseModel):
    text: str


@app.post("/api/engagements/import")
def import_engagements(
    body: ImportIn,
    account: Account = Depends(accounts_gate.current_account),
    db: Session = Depends(get_db),
):
    """Read a pasted list of clients and projects (an outline from a notes app, say) and PROPOSE engagements from it. Nothing is
    saved: the page shows them for the person to tick and add, through the ordinary create route. Their own Anthropic key pays."""
    choice = apikeys.pick(db, account)
    try:
        limits.start(("import", account.id))
    except limits.TooBusy as e:
        raise HTTPException(status_code=429, detail=str(e))
    used: list = []  # tokens, to count the run if it is on the system key
    try:
        return {"engagements": generate.extract_engagements(choice.key, body.text, usage=used), "room": max(0, builder.MAX_ENGAGEMENTS - len(builder.list_engagements(db)))}
    except generate.ResumeGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if choice.metered:
            crud.record_house_use(db, account.id, "import", used)
        limits.finish(("import", account.id))


@app.put("/api/engagements/{engagement_id}", response_model=builder.EngagementOut)
def update_engagement(engagement_id: int, body: builder.EngagementIn, db: Session = Depends(get_db)):
    row = builder.update_engagement(db, engagement_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="Engagement not found")
    return builder.to_out(row)


@app.delete("/api/engagements")
def delete_all_engagements(confirm: str = "", db: Session = Depends(get_db)):
    """Remove ALL of this person's engagements. Needs `?confirm=all`, so a stray call (or a forgotten id on the single-delete URL)
    can never wipe them. The page asks the person first."""
    if confirm != "all":
        raise HTTPException(status_code=400, detail="Removing every engagement needs confirm=all.")
    return {"removed": builder.delete_all_engagements(db)}


@app.delete("/api/engagements/{engagement_id}")
def delete_engagement(engagement_id: int, db: Session = Depends(get_db)):
    if not builder.delete_engagement(db, engagement_id):
        raise HTTPException(status_code=404, detail="Engagement not found")
    return {"ok": True}


@app.get("/api/builder/notes")
def get_notes(db: Session = Depends(get_db)):
    return {"notes": builder.get_notes(db), "keep_files": builder.keeps_files(db)}


class KeepIn(BaseModel):
    keep: bool


@app.put("/api/builder/keep")
def put_keep(body: KeepIn, db: Session = Depends(get_db)):
    """Whether the builder keeps this person's files and draft. Turning it off deletes what is already kept."""
    return {"keep_files": builder.set_keep_files(db, body.keep)}


def _must_keep(db: Session) -> None:
    if not builder.keeps_files(db):
        raise HTTPException(status_code=409, detail="You chose not to keep files on your account. Turn that on to keep them.")


@app.put("/api/builder/notes")
def put_notes(body: builder.NotesIn, db: Session = Depends(get_db)):
    return {"notes": builder.save_notes(db, body.notes)}


class PreviewIn(BaseModel):
    resume_yaml: str
    base_yaml: Optional[str] = None  # when updating: the resume as it is now, so what the draft changes can be shown


def _changes(before: str, after: str, limit: int = 400) -> list:
    """The lines an update adds and removes, with a line of context around each, as [{op, text}] (op is "+", "-" or " ")."""
    lines = []
    for line in difflib.unified_diff(before.strip().splitlines(), after.strip().splitlines(), lineterm="", n=1):
        if line.startswith(("---", "+++")):
            continue
        lines.append({"op": line[:1] if line[:1] in "+-@" else " ", "text": line[1:] if line[:1] in "+- " else line})
    return lines[:limit]


@app.post("/api/builder/preview")
def preview_resume(body: PreviewIn):
    """A readable summary of a draft, for the review step, or exactly what is wrong with it (the person is editing YAML)."""
    try:
        data = yaml.safe_load(body.resume_yaml)
    except yaml.YAMLError as e:
        return {"ok": False, "error": f"That isn't valid YAML yet: {str(e).splitlines()[0] if str(e) else 'syntax error'}"}
    if not isinstance(data, dict) or not isinstance(data.get("basics"), dict):
        return {"ok": False, "error": "It needs a top-level 'basics' section."}
    basics = data["basics"]
    jobs = [j for j in (data.get("experience") or []) if isinstance(j, dict)]
    return {
        "ok": True,
        "name": basics.get("name", ""),
        "title": basics.get("title", ""),
        "counts": {
            "Jobs": len(jobs),
            "Bullets": sum(len(j.get("bullets") or []) for j in jobs),
            "Engagements": sum(len(j.get("engagements") or []) for j in jobs),
            "Education": len(data.get("education") or []),
            "Skills": sum(len((s or {}).get("items") or []) for s in (data.get("skills") or []) if isinstance(s, dict)),
            "Certifications": len(data.get("certifications") or []),
            "Projects": len(data.get("projects") or []),
        },
        "jobs": [
            {"title": j.get("title", ""), "company": j.get("company", ""), "dates": f"{j.get('start', '')} - {j.get('end', '')}".strip(" -"),
             "bullets": [str(b) for b in (j.get("bullets") or [])][:12]}
            for j in jobs
        ],
        "changes": _changes(body.base_yaml, body.resume_yaml) if body.base_yaml else None,
        "suggestions": feedback.check_resume(data),
    }


class ReviewIn(BaseModel):
    resume_yaml: str


@app.post("/api/builder/review")
def review_resume(
    body: ReviewIn,
    account: Account = Depends(accounts_gate.current_account),
    db: Session = Depends(get_db),
):
    """An AI editor's suggestions on the draft being reviewed ([{where, issue, suggestion}], maybe empty). Nothing is saved or changed;
    only what prints is sent (not private engagements, not jobs left off the resume). Their own Anthropic key pays, and it is counted
    separately from drafting so reviewing a few edits never uses up the drafts."""
    choice = apikeys.pick(db, account)
    try:
        limits.start(("review", account.id))
    except limits.TooBusy as e:
        raise HTTPException(status_code=429, detail=str(e).replace("draft", "review"))
    used: list = []
    try:
        return {"suggestions": generate.review_resume(choice.key, body.resume_yaml, usage=used)}
    except generate.ResumeGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if choice.metered:
            crud.record_house_use(db, account.id, "review", used)
        limits.finish(("review", account.id))


@app.post("/api/generate-resume")
def generate_resume_route(
    sources: list[UploadFile] = File(default=[]),
    update_person_id: Optional[int] = Form(default=None),
    account: Account = Depends(accounts_gate.current_account),
    db: Session = Depends(get_db),
):
    """START drafting a resume from everything the person has STORED (the text of their files, their engagements and their notes: none
    of it taken from the request, so what was saved is what is used; adding one engagement and drafting again is all an update needs).
    With `update_person_id`, the draft is a merge into that resume of theirs (one they own: anyone else's is a 404), keeping their edits.
    This returns at once with the job's state; the work runs in the background (app/drafts.py) because it takes longer than a proxy
    will wait for one request. The page then asks `/api/generate-resume/status` until it is done. The result is kept as their unsaved
    draft (unless they chose not to keep files), so a reload does not lose it."""
    choice = apikeys.pick(db, account)  # their own key (an administrator, or someone an administrator allowed, may use the server's)
    base_yaml = None
    if update_person_id is not None:
        base = crud.get_person(db, update_person_id)
        if base is None:
            raise HTTPException(status_code=404, detail="Person not found")
        base_yaml = base.resume_yaml
    engagement_list = builder.for_prompt(db)
    notes = builder.get_notes(db)
    linkedin_text, documents = builder.sources_for_prompt(db)
    if sources:  # someone who chose not to keep files sends them with each draft; they are read in memory and never stored
        try:
            sent_linkedin, sent_documents = generate.prepare_sources(_read_uploads(sources))
        except generate.ResumeGenerationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        linkedin_text = "\n\n".join(t for t in (linkedin_text, sent_linkedin) if t) or None
        documents = documents + sent_documents
    try:
        limits.start(account.id)
    except limits.TooBusy as e:
        raise HTTPException(status_code=429, detail=str(e))
    keep, account_id = builder.keeps_files(db), account.id

    def work() -> str:  # runs in its own thread, so it opens its own session and scopes it to this person, as a request would
        used: list = []
        try:
            resume_yaml = generate.generate_resume_yaml(choice.key, linkedin_text, documents, notes, engagement_list, base_yaml, usage=used)
        finally:
            if choice.metered:  # a run on the system key is counted whether or not it worked: it was paid for
                with SessionLocal() as session:
                    crud.record_house_use(session, account_id, "draft", used)
        if keep:
            with owned.scope(account_id), SessionLocal() as session:
                builder.save_draft(session, resume_yaml, update_person_id)
        return resume_yaml

    try:
        drafts.start(account_id, work, update_person_id)
    except Exception:
        limits.finish(account_id)
        raise
    return drafts.status(account_id)


@app.get("/api/generate-resume/status")
def generate_resume_status(account: Account = Depends(accounts_gate.current_account)):
    """How this person's draft is going: {state: idle | running | done | error, ...}. A finished one carries its resume_yaml."""
    return drafts.status(account.id)


@app.delete("/api/generate-resume/status")
def clear_generate_resume_status(account: Account = Depends(accounts_gate.current_account)):
    """The page has shown the finished (or failed) draft: forget it. A draft still running is left alone."""
    drafts.clear(account.id)
    return drafts.status(account.id)


# --- People: browser-facing CRUD for the admin UI (admin_static/). Each account sees and changes ONLY its own resumes (the
# owner filter, shared_python.owned, applies below these routes). An ordinary account has exactly one resume, with a slug
# nobody chooses, never the site's default and never published; an administrator may have several, choose slugs, set the
# default and publish. This is the only place resume content is ever created or edited; there is deliberately no
# file/upload-to-disk path anywhere in this app any more.


def _rebuild_person_site(person) -> None:
    """Regenerates this one person's site/private/<slug>/ and site/public/<slug>/ trees - but only for a PUBLISHED person, since
    those trees exist for the public site; anyone else's are removed. Same layout build.py (the deploy-time CLI step) produces.
    Called after a Person create/update, since editing their resume_yaml (or publishing them) is what changes what their
    slug-keyed tree should show. Switching who's *default* doesn't need a rebuild: every published person's tree already exists
    at their own slug, only which slug app/main.py resolves to for a bare (no ?person=) request changes. Best-effort: logs and
    swallows a rendering failure (e.g. a transient Playwright hiccup) rather than losing the Person write that already
    succeeded - the site can always be rebuilt by hand (`python build.py`) or by the next deploy either way."""
    if not person.published:
        _remove_person_site(person.slug)
        return
    try:
        data = yaml.safe_load(person.resume_yaml)
        render.build_tree(data, SITE_DIR / "private" / person.slug)
        render.build_tree(render.redact(data), SITE_DIR / "public" / person.slug)
    except Exception as e:
        print(f"warning: site rebuild for {person.slug!r} failed: {e}")


def _remove_person_site(slug: str) -> None:
    """Deletes a person's site/{private,public}/<slug>/ trees - after a delete or an un-publish."""
    shutil.rmtree(SITE_DIR / "private" / slug, ignore_errors=True)
    shutil.rmtree(SITE_DIR / "public" / slug, ignore_errors=True)


class PersonCreateRequest(BaseModel):
    slug: Optional[str] = None  # administrators only; everyone else gets one made for them
    name: str
    resume_yaml: str
    is_default: bool = False  # administrators only
    published: Optional[bool] = None  # administrators only (default: published, as before accounts); never for anyone else


class PersonUpdateRequest(BaseModel):
    name: Optional[str] = None
    resume_yaml: Optional[str] = None
    is_default: Optional[bool] = None  # administrators only
    published: Optional[bool] = None  # administrators only


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    is_default: bool
    published: bool = False
    created_at: datetime
    updated_at: datetime
    # resume_yaml deliberately left out of the list shape - could be a few
    # KB per person; fetched separately (PersonDetailOut) only when actually
    # editing one.


class PersonDetailOut(PersonOut):
    resume_yaml: str


def _admin_only(account: Account, what: str) -> None:
    if not account.is_admin:
        raise HTTPException(status_code=403, detail=f"Only an administrator can {what}")


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
def create_person_route(
    body: PersonCreateRequest, db: Session = Depends(get_db), account: Account = Depends(accounts_gate.current_account)
):
    if account.is_admin:
        if not body.slug:
            raise HTTPException(status_code=400, detail="Choose a slug (the short name used in the public address)")
        slug, published = body.slug, True if body.published is None else body.published
    else:
        if body.slug or body.is_default or body.published:
            _admin_only(account, "choose a slug, set the default or publish a resume")
        if crud.list_people(db):
            raise HTTPException(status_code=409, detail="You already have a resume - edit it instead")
        slug, published = crud.private_slug(account.id), False
    try:
        person = crud.create_person(db, slug, body.name, body.resume_yaml, body.is_default, allow_default=account.is_admin)
        if published:
            person = crud.update_person(db, person.id, published=True)
    except crud.PersonError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _rebuild_person_site(person)
    return person


@app.patch("/api/people/{person_id}", response_model=PersonDetailOut)
def update_person_route(
    person_id: int, body: PersonUpdateRequest, db: Session = Depends(get_db), account: Account = Depends(accounts_gate.current_account)
):
    if body.is_default is not None or body.published is not None:
        _admin_only(account, "set the default or publish a resume")
    try:
        person = crud.update_person(
            db, person_id, name=body.name, resume_yaml=body.resume_yaml, is_default=body.is_default, published=body.published
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


@app.get("/api/people/{person_id}/download")
def download_person_route(person_id: int, format: Literal["pdf", "docx"] = "pdf", db: Session = Depends(get_db)):
    """The signed-in person's own resume as a PDF or DOCX, with everything in it (it is theirs), rendered on the spot. Nothing is
    written to the public site."""
    person = crud.get_person(db, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    try:
        data = yaml.safe_load(person.resume_yaml)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            if format == "pdf":
                render.build_pdf(data, out_dir)
                body, media = (out_dir / "resume.pdf").read_bytes(), "application/pdf"
            else:
                render.build_docx(data, out_dir)
                body = (out_dir / "resume.docx").read_bytes()
                media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not render this resume: {e}")
    base = re.sub(r"[^A-Za-z0-9 _-]", " ", person.name)
    stem = re.sub(r"\s+", "_", base.strip()) or "Resume"
    filename = f"{stem}_Resume.{format}"
    return Response(content=body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"})


# --- Access codes: administrators only. A code unlocks a PUBLISHED person's contact details on the public site.


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


@app.get("/api/codes", response_model=list[CodeOut], dependencies=[Depends(accounts_gate.require_admin)])
def list_codes(db: Session = Depends(get_db)):
    return [_to_out(c, db) for c in crud.list_codes(db)]


@app.post("/api/codes", response_model=CodeOut, dependencies=[Depends(accounts_gate.require_admin)])
def create_code(body: CreateCodeRequest, db: Session = Depends(get_db)):
    person_id = body.person_id
    if person_id is None:
        default_person = crud.get_default_person(db)
        person_id = default_person.id if default_person else None
    elif crud.get_person(db, person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    access_code = crud.generate_code(db, label=body.label, days_valid=body.days, person_id=person_id)
    return _to_out(access_code, db)


@app.post("/api/codes/{code_id}/revoke", response_model=CodeOut, dependencies=[Depends(accounts_gate.require_admin)])
def revoke_code_route(code_id: int, db: Session = Depends(get_db)):
    access_code = crud.revoke_code(db, str(code_id))
    if access_code is None:
        raise HTTPException(status_code=404, detail="Code not found or already revoked")
    return _to_out(access_code, db)


app.mount("/", StaticFiles(directory=ADMIN_STATIC_DIR, html=True), name="static")
