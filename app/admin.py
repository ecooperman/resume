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
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from . import crud, render
from .deps import get_db

ADMIN_STATIC_DIR = Path(__file__).parent.parent / "admin_static"

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


@app.get("/api/resume-data", dependencies=[Depends(require_internal_token)])
def get_resume_data():
    """The current resume.yaml, as JSON - source data for tailoring a resume
    to a specific job elsewhere. Read fresh from disk on every call, so it's
    always in sync with whatever's actually in resume.yaml right now."""
    return render.load_data()


@app.post("/api/render", dependencies=[Depends(require_internal_token)])
def render_document(body: RenderRequest):
    """Render arbitrary resume-shaped data (e.g. a job-tailored variant, not
    necessarily this repo's own resume.yaml) into a PDF or DOCX. Always
    writes into a throwaway temp directory - this never touches site/ or
    resume.yaml, so it can't affect the real resume site."""
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


class CreateCodeRequest(BaseModel):
    label: Optional[str] = None
    days: Optional[int] = None


class CodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    label: Optional[str]
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


def _to_out(access_code) -> CodeOut:
    out = CodeOut.model_validate(access_code)
    out.status = _status(access_code)
    return out


@app.get("/api/codes", response_model=list[CodeOut])
def list_codes(db: Session = Depends(get_db)):
    return [_to_out(c) for c in crud.list_codes(db)]


@app.post("/api/codes", response_model=CodeOut)
def create_code(body: CreateCodeRequest, db: Session = Depends(get_db)):
    access_code = crud.generate_code(db, label=body.label, days_valid=body.days)
    return _to_out(access_code)


@app.post("/api/codes/{code_id}/revoke", response_model=CodeOut)
def revoke_code_route(code_id: int, db: Session = Depends(get_db)):
    access_code = crud.revoke_code(db, str(code_id))
    if access_code is None:
        raise HTTPException(status_code=404, detail="Code not found or already revoked")
    return _to_out(access_code)


app.mount("/", StaticFiles(directory=ADMIN_STATIC_DIR, html=True), name="static")
