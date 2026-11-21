"""Send feedback: anyone with an account can send the administrator a message (a problem, an idea or a question) from one page. It is saved
(so it cannot be lost if an email or phone push fails) and the administrator is told; they read and tick off messages on the same page and
reply by email to the address it shows. A person sees only their own messages; the box of everyone's is administrators-only. Kept small on
purpose: a handful a day each, nothing attached."""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from shared_python import accounts_gate
from shared_python.accounts_gate import Account
from sqlalchemy.orm import Session

from . import crud, models, notify
from .deps import get_db

MAX_PER_DAY = 10
MAX_BODY = 4000
KIND_LABELS = {"problem": "Problem", "idea": "Idea", "question": "Question"}

router = APIRouter(prefix="/api/admin/messages", tags=["messages"], dependencies=[Depends(accounts_gate.require_admin)])
app_router = APIRouter(prefix="/api/contact", tags=["messages"])


class MessageIn(BaseModel):
    kind: str
    body: str = Field(max_length=MAX_BODY * 2)  # trimmed and checked below, so the person gets a sentence rather than a validation error
    page: Optional[str] = Field(default=None, max_length=300)


class MyMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    body: str
    page: Optional[str] = None
    created_at: datetime
    done_at: Optional[datetime] = None


class AdminMessage(MyMessage):
    from_email: str


def _admin_view(message: models.Message) -> AdminMessage:
    return AdminMessage(id=message.id, kind=message.kind, body=message.body, page=message.page, created_at=message.created_at,
                        done_at=message.done_at, from_email=message.user.email)


def _page(value: Optional[str]) -> Optional[str]:
    """Only a path on this app ("/jobs.html") is kept, so a message cannot carry a stranger's link."""
    value = (value or "").strip()
    return value[:100] if value.startswith("/") and not value.startswith("//") else None


@app_router.post("", response_model=MyMessage)
def send_message(body: MessageIn, background: BackgroundTasks, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    kind, text = body.kind.strip().lower(), body.body.strip()
    if kind not in models.MESSAGE_KINDS:
        raise HTTPException(status_code=400, detail="Choose what kind of message this is: a problem, an idea or a question.")
    if len(text) < 3:
        raise HTTPException(status_code=400, detail="Write a few words first.")
    if len(text) > MAX_BODY:
        raise HTTPException(status_code=400, detail=f"That message is {len(text):,} characters; the limit is {MAX_BODY:,}. Please shorten it.")
    if crud.messages_sent_since(db, account.id, datetime.utcnow() - timedelta(days=1)) >= MAX_PER_DAY:
        raise HTTPException(status_code=429, detail=f"You have sent {MAX_PER_DAY} messages today. Please try again tomorrow, or add to your last one by email.")
    message = crud.create_message(db, account.id, kind, text, _page(body.page))
    background.add_task(notify.feedback_received, account.email, KIND_LABELS[kind], text)
    return message


@app_router.get("", response_model=List[MyMessage])
def my_messages(account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """What this person has sent, newest first, so they can see it went."""
    return crud.my_messages(db, account.id)


@router.get("", response_model=List[AdminMessage])
def list_messages(db: Session = Depends(get_db)):
    return [_admin_view(m) for m in crud.all_messages(db)]


def _one(db: Session, message_id: int) -> models.Message:
    message = crud.get_message(db, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


@router.post("/{message_id}/done", response_model=AdminMessage)
def mark_done(message_id: int, db: Session = Depends(get_db)):
    return _admin_view(crud.set_message_done(db, _one(db, message_id), True))


@router.post("/{message_id}/reopen", response_model=AdminMessage)
def reopen(message_id: int, db: Session = Depends(get_db)):
    return _admin_view(crud.set_message_done(db, _one(db, message_id), False))


@router.delete("/{message_id}")
def delete_message(message_id: int, db: Session = Depends(get_db)):
    crud.remove_message(db, _one(db, message_id))
    return {"ok": True}
