"""Personal tokens for the job-scoring Chrome extension (the signed-in person's own; see app/push.py for what a token opens). The token
itself is returned once, when it is made, and never again: only a hash is kept. Removing one revokes it at once."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import crud, models
from .deps import get_db

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenIn(BaseModel):
    name: str


def _out(row: models.PushToken) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None  # stored as naive UTC
    return {"id": row.id, "name": row.name, "prefix": row.prefix, "created_at": iso(row.created_at), "last_used_at": iso(row.last_used_at)}


@router.get("")
def list_tokens(db: Session = Depends(get_db)):
    return [_out(t) for t in crud.list_tokens(db)]


@router.post("")
def make_token(body: TokenIn, db: Session = Depends(get_db)):
    try:
        row, raw = crud.create_token(db, body.name)
    except crud.JobError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**_out(row), "token": raw}  # the only time it is shown


@router.delete("/{token_id:int}")
def revoke_token(token_id: int, db: Session = Depends(get_db)):
    if not crud.delete_token(db, token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return {"ok": True}
