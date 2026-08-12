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
        filename="Evan_Cooperman_Resume.pdf",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/resume.docx")
def resume_docx(code: Optional[str] = None, db: Session = Depends(get_db)):
    directory = _resolve_dir(code, db)
    return FileResponse(
        directory / "resume.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="Evan_Cooperman_Resume.docx",
        headers=NO_CACHE_HEADERS,
    )
