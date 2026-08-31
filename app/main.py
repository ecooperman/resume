"""
Serves a resume in one of two forms:

  - A valid ?code=...: since a code is tied to exactly one person (see
    AccessCode's docstring), it's looked up first and is authoritative on
    its own - whoever it belongs to, private tree (full contact info, real
    downloads), full stop. There's nothing else to get right or wrong: no
    separate "which person" to also specify and have possibly disagree
    with the code - the code just *is* the answer.
  - No code, or an invalid/expired/revoked one: the public/ tree for
    whichever person ?person=<slug> names (or the current default person
    if omitted) -- no email, phone, or location, generated with contact
    info stripped at build time (see build.py).

Every person's trees are pre-built by `python build.py` into
site/public/<slug>/ and site/private/<slug>/ (or by app/admin.py directly,
right after an admin-UI edit to that person); this app just decides which
pre-built file to hand back and records that a code was used. No PDF/DOCX
generation happens per-request.
"""
import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import crud
from .deps import get_db

SITE_DIR = Path(__file__).parent.parent / "site"

app = FastAPI()


def _resolve(person_slug: Optional[str], code: Optional[str], db: Session):
    """Figures out which person this request is about and which of their
    pre-built trees to serve, recording code usage on success. Every route
    calls this independently -- there's no session/cookie, so a direct
    request to /resume.pdf?code=... is checked exactly the same as a
    request to /?code=.... 404s for an unknown ?person=<slug>, or for no
    default person existing yet (only possible before anyone's been set up
    through the admin UI at all) - a clean error here instead of crashing
    further down on a pre-built file that was never written."""
    if code:
        access_code = crud.get_valid_code(db, code)
        if access_code is not None and access_code.person_id is not None:
            person = crud.get_person(db, access_code.person_id)
            if person is not None:
                crud.touch_code(db, access_code)
                return person, SITE_DIR / "private" / person.slug
    # No code, or one that didn't resolve to a real person - public
    # browsing by slug (or the default person if none given).
    person = crud.get_person_by_slug(db, person_slug) if person_slug else crud.get_default_person(db)
    if person is None:
        raise HTTPException(status_code=404, detail="No such person")
    return person, SITE_DIR / "public" / person.slug


# Every route here is code-gated per request (see _resolve) - nothing from
# these routes should ever be cacheable by Cloudflare's edge or a browser.
# Without an explicit Cache-Control, Cloudflare falls back to its own
# default caching for static-looking extensions like .pdf/.docx (HTML
# isn't cached by default, which is why a stale-download report with an
# already-fresh page is the telltale symptom) - and a cached private
# response would keep being served after a code is revoked, since the CDN
# never re-asks the origin/DB during the cache window.
NO_CACHE_HEADERS = {"Cache-Control": "private, no-store"}


def _download_filename(person, ext: str) -> str:
    """The name a download is offered under - was hardcoded to one
    person's name regardless of who the site was actually serving.
    Derived from whichever person is actually being served - name is
    already visible on the public page itself (redact() only strips
    email/phone/location), so this doesn't expose anything the page
    doesn't already show."""
    base = re.sub(r"[^A-Za-z0-9 _-]", " ", person.name)
    base = re.sub(r"\s+", "_", base.strip()) or "Resume"
    return f"{base}_Resume.{ext}"


@app.get("/")
def index(person: Optional[str] = None, code: Optional[str] = None, db: Session = Depends(get_db)):
    _, directory = _resolve(person, code, db)
    return FileResponse(directory / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/resume.pdf")
def resume_pdf(person: Optional[str] = None, code: Optional[str] = None, db: Session = Depends(get_db)):
    target, directory = _resolve(person, code, db)
    return FileResponse(
        directory / "resume.pdf",
        media_type="application/pdf",
        filename=_download_filename(target, "pdf"),
        headers=NO_CACHE_HEADERS,
    )


@app.get("/resume.docx")
def resume_docx(person: Optional[str] = None, code: Optional[str] = None, db: Session = Depends(get_db)):
    target, directory = _resolve(person, code, db)
    return FileResponse(
        directory / "resume.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=_download_filename(target, "docx"),
        headers=NO_CACHE_HEADERS,
    )
