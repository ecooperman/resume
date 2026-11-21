"""Asking for someone to be let in. Anyone with an account may ask for a friend (up to INVITES_PER_USER different people; administrators
have no limit and their own requests are approved at once); an administrator approves or declines each on the Invites page, and an
approved person gets an account and an email saying how to sign in. The same behaviour as Lists and Trips; the one difference is that
this app sits behind the dashboard's sign-in, so approving only creates the account here (nothing is added to Cloudflare).

Nobody's request lets them see anyone else's: a person reads and withdraws only their own, the queue is administrators-only, and a
resume stays private to its owner whoever invited whom."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from shared_python import accounts_gate
from shared_python.accounts_gate import Account
from sqlalchemy.orm import Session

from . import config, crud, models, notify
from .deps import get_db

# The administrator's side: the requests waiting for a decision, and deciding.
router = APIRouter(prefix="/api/admin/invites", tags=["invites"], dependencies=[Depends(accounts_gate.require_admin)])
# Anyone with an account: ask for a person, see and withdraw your own requests.
app_router = APIRouter(prefix="/api/app-invites", tags=["invites"])


class AppInvite(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    status: str
    created_at: Optional[datetime] = None


class AdminInvite(AppInvite):
    decided_at: Optional[datetime] = None
    invited_by_email: str


class AppInviteCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)


def _admin_view(invite: models.Invite) -> AdminInvite:
    return AdminInvite(id=invite.id, email=invite.email, status=invite.status, created_at=invite.created_at,
                       decided_at=invite.decided_at, invited_by_email=invite.invited_by.email)


def approve(db: Session, invite: models.Invite, background: BackgroundTasks) -> models.Invite:
    """Give the person an account (or restore a removed one) and tell them how to sign in. Someone who already has access by now (an
    administrator added them meanwhile) is simply marked approved and not emailed a second time."""
    inviter_email = invite.invited_by.email
    user, changed = crud.add_user(db, invite.email)
    crud.decide_invite(db, invite, models.INVITE_APPROVED)
    if changed:
        background.add_task(notify.tell_let_in, user.email, inviter_email)
    return invite


def _waiting(db: Session, invite_id: int) -> models.Invite:
    invite = crud.get_invite(db, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status != models.INVITE_PENDING:
        raise HTTPException(status_code=409, detail=f"That request was already {invite.status}")
    return invite


@router.get("", response_model=List[AdminInvite])
def list_waiting(db: Session = Depends(get_db)):
    return [_admin_view(i) for i in crud.invites_with_status(db, models.INVITE_PENDING)]


@router.get("/declined", response_model=List[AdminInvite])
def list_declined(db: Session = Depends(get_db)):
    """Requests that were declined, so the administrator can change their mind."""
    return [_admin_view(i) for i in crud.invites_with_status(db, models.INVITE_DECLINED)]


@router.post("/{invite_id}/approve", response_model=AdminInvite)
def approve_invite(invite_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    return _admin_view(approve(db, _waiting(db, invite_id), background))


@router.post("/{invite_id}/decline", response_model=AdminInvite)
def decline_invite(invite_id: int, db: Session = Depends(get_db)):
    return _admin_view(crud.decide_invite(db, _waiting(db, invite_id), models.INVITE_DECLINED))


@router.post("/{invite_id}/reinstate")
def reinstate_invite(invite_id: int, db: Session = Depends(get_db)):
    """Allow a declined person to be asked for again: the declined request is removed, so anyone may ask once more (and the asker gets
    that invite back)."""
    invite = crud.get_invite(db, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status != models.INVITE_DECLINED:
        raise HTTPException(status_code=409, detail="Only a declined request can be allowed again")
    crud.remove_invite(db, invite)
    return {"ok": True}


@app_router.get("", response_model=List[AppInvite])
def my_app_invites(account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """The requests this person made, in any state."""
    return crud.my_invites(db, account.id)


@app_router.post("", response_model=AppInvite)
def ask_for_someone(
    body: AppInviteCreate,
    background: BackgroundTasks,
    account: Account = Depends(accounts_gate.current_account),
    db: Session = Depends(get_db),
):
    """Ask for someone with no account to be let in. The administrator is told and decides; an administrator's own request is approved
    straight away."""
    email = crud.normalize_email(body.email)
    if not crud.valid_email(email):
        raise HTTPException(status_code=400, detail="That doesn't look like an email address")
    existing = crud.get_user_by_email(db, email)
    if existing is not None and existing.status == models.USER_REMOVED:
        raise HTTPException(status_code=409, detail=f"{email}'s access to Jobs was removed. Only an administrator can restore it.")
    if (existing is not None and existing.status == models.USER_ACTIVE) or email in config.ADMIN_EMAILS:
        raise HTTPException(status_code=409, detail=f"{email} can already sign in, so there's nothing to request.")
    invite, problem = crud.create_invite(db, account.id, account.is_admin, email)
    if problem == "duplicate":
        earlier = db.query(models.Invite).filter(models.Invite.email == email).first()
        if earlier is not None and earlier.status == models.INVITE_DECLINED:
            detail = f"A request for {email} was declined. Only an administrator can let them in now."
        else:
            detail = f"A request for {email} is already waiting for an administrator to approve."
        raise HTTPException(status_code=409, detail=detail)
    if problem == "none_left":
        raise HTTPException(status_code=403, detail=f"You've used all {config.INVITES_PER_USER} of your invites. Only an administrator can raise the limit.")
    invite = crud.get_invite(db, invite.id)  # with who asked, for the email below
    if account.is_admin:
        return approve(db, invite, background)
    background.add_task(notify.invite_requested, account.email, invite.email)
    return invite


@app_router.delete("/{invite_id}")
def withdraw_invite(invite_id: int, account: Account = Depends(accounts_gate.current_account), db: Session = Depends(get_db)):
    """Withdraw one of your own requests that is still waiting (this gives the invite back)."""
    invite = crud.get_invite(db, invite_id)
    if invite is None or invite.invited_by_id != account.id:
        raise HTTPException(status_code=404, detail="Invite not found")  # not yours: indistinguishable from one that does not exist
    if invite.status != models.INVITE_PENDING:
        raise HTTPException(status_code=409, detail="Only a request that's still waiting can be withdrawn")
    crud.remove_invite(db, invite)
    return {"ok": True}
