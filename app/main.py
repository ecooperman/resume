"""
Serves the resume in two forms depending on whether a valid access code is
present:

  - No code, or an invalid/expired/revoked one: the public/ tree -- no
    email, phone, or location, generated with contact info stripped at
    build time (see build.py).
  - A valid ?code=... (checked against the access_codes table on every
    single request, not just the page load): the private/ tree -- full
    contact info, real downloads.

Both trees are pre-built by `python build.py` into site/public/ and
site/private/; this app just decides which pre-built file to hand back and
records that the code was used. No PDF/DOCX generation happens per-request.
"""
import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import crud
from .deps import get_db

SITE_DIR = Path(__file__).parent.parent / "site"
PUBLIC_DIR = SITE_DIR / "public"
PRIVATE_DIR = SITE_DIR / "private"

app = FastAPI()


def _resolve_dir(code: Optional[str], db: Session) -> Path:
    """Validate the code (if any) and return which pre-built tree to serve
    from, recording usage on success. Every route calls this independently
    -- there's no session/cookie, so a direct request to /resume.pdf?code=...
    is checked exactly the same as a request to /."""
    if code:
        access_code = crud.get_valid_code(db, code)
        if access_code is not None:
            crud.touch_code(db, access_code)
            return PRIVATE_DIR
    return PUBLIC_DIR


# Every route here is code-gated per request (see _resolve_dir) - nothing
# from these routes should ever be cacheable by Cloudflare's edge or a
# browser. Without an explicit Cache-Control, Cloudflare falls back to its
# own default caching for static-looking extensions like .pdf/.docx (HTML
# isn't cached by default, which is why a stale-download report with an
# already-fresh page is the telltale symptom) - and a cached private
# response would keep being served after a code is revoked, since the
# CDN never re-asks the origin/DB during the cache window.
NO_CACHE_HEADERS = {"Cache-Control": "private, no-store"}


def _download_filename(db: Session, ext: str) -> str:
    """The name a download is offered under - was hardcoded to
    "Evan_Cooperman_Resume.*" regardless of who the site was actually
    serving, so switching the default person (see app/admin.py's
    _rebuild_site) fixed the page content but downloads still had the
    wrong person's name on them. Derived from the *current* default
    person, same source of truth _rebuild_site uses, not baked in at build
    time - name is already visible on the public page itself (redact()
    only strips email/phone/location), so this doesn't expose anything
    the page doesn't already show."""
    person = crud.get_default_person(db)
    if person is None:
        return f"Resume.{ext}"
    base = re.sub(r"[^A-Za-z0-9 _-]", " ", person.name)
    base = re.sub(r"\s+", "_", base.strip()) or "Resume"
    return f"{base}_Resume.{ext}"


@app.get("/")
def index(code: Optional[str] = None, db: Session = Depends(get_db)):
    directory = _resolve_dir(code, db)
    return FileResponse(directory / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/resume.pdf")
def resume_pdf(code: Optional[str] = None, db: Session = Depends(get_db)):
    directory = _resolve_dir(code, db)
    return FileResponse(
        directory / "resume.pdf",
        media_type="application/pdf",
        filename=_download_filename(db, "pdf"),
        headers=NO_CACHE_HEADERS,
    )


@app.get("/resume.docx")
def resume_docx(code: Optional[str] = None, db: Session = Depends(get_db)):
    directory = _resolve_dir(code, db)
    return FileResponse(
        directory / "resume.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=_download_filename(db, "docx"),
        headers=NO_CACHE_HEADERS,
    )
