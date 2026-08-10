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
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from . import crud
from .deps import get_db

ADMIN_STATIC_DIR = Path(__file__).parent.parent / "admin_static"

app = FastAPI(title="Resume Admin")


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
