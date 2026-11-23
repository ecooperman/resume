"""A person's own Anthropic key, which drafts their resume from the documents they give it.

Each person brings their own key, so nobody is metered on the owner's bill. Which key a draft uses:

1. the signed-in person's own saved key, if they have one;
2. otherwise the server's own key from the environment, but ONLY for an administrator, or for a person an administrator has
   allowed (the Users page; counted, a monthly allowance each, see HOUSE_KEY_CALLS_PER_MONTH). Anyone else without a key of their
   own simply cannot draft, and is told how to turn it on.

A person saves their key ONCE, on their Brassworks profile (the dashboard, dashboard/app/keys.py), where it is encrypted and never sent back to a
browser; this app keeps none. It asks the dashboard for it when it is about to use it, forwarding the person's own sign-in cookie
(shared_python.user_keys), so it can only ever be theirs. This module is the same idea as trip-planning's apikeys.py, for the one provider this
app calls."""
import logging
from typing import NamedTuple, Optional

from fastapi import HTTPException
from shared_python import user_keys
from sqlalchemy.orm import Session

from . import config, crud

log = logging.getLogger(__name__)

PROVIDER = "anthropic"
GET_KEY_URL = "https://console.anthropic.com/settings/keys"


class KeyChoice(NamedTuple):
    key: str
    metered: bool  # True when it is the server's key used by an allowed (non-administrator) person: the run is counted against their month


def _own_key(db=None) -> Optional[str]:
    """The key this person saved on the dashboard, or None. Fetched with their own sign-in cookie for this request (or the session a
    background job was handed, see scoring.py); a dashboard that cannot be reached is an HTTP 503 with a sentence, never a missing key.
    (Tests replace this function to give each test person a key.)"""
    return user_keys.fetch(PROVIDER)


def _saved() -> Optional[dict]:
    """What they saved ({"last4", "added_at"}), for showing what is set. A dashboard that cannot be reached says nothing is saved, rather than
    failing a page that does not need the key."""
    try:
        return user_keys.status().get(PROVIDER)
    except HTTPException:
        return None


def _house_grant(db: Session, account) -> bool:
    """Whether this non-administrator has been allowed to use the server's key (and there is one to use)."""
    if account.is_admin or not config.ANTHROPIC_API_KEY:
        return False
    user = crud.get_user(db, account.id)
    return bool(user is not None and user.house_key)


def runs_left(db: Session, account) -> Optional[int]:
    """Runs left this month on the server's key, or None when there is no limit (the default)."""
    if config.HOUSE_KEY_CALLS_PER_MONTH <= 0:
        return None
    return max(config.HOUSE_KEY_CALLS_PER_MONTH - crud.house_uses_this_month(db, account.id), 0)


def pick(db: Session, account) -> KeyChoice:
    """The key a run uses: their own saved one if they have one; otherwise the server's for an administrator (never counted) or for a
    person an administrator allowed (counted, up to HOUSE_KEY_CALLS_PER_MONTH a month); otherwise they are told what to do."""
    own = _own_key(db)
    if own:
        return KeyChoice(own, False)
    if account.is_admin and config.ANTHROPIC_API_KEY:
        return KeyChoice(config.ANTHROPIC_API_KEY, False)
    if _house_grant(db, account):
        left = runs_left(db, account)
        if left is not None and left <= 0:
            raise HTTPException(status_code=429, detail=f"You've used your {config.HOUSE_KEY_CALLS_PER_MONTH} AI runs on the system key this month. They start again next month; or add your own Anthropic key in step 1 to carry on now.")
        return KeyChoice(config.ANTHROPIC_API_KEY, True)
    raise HTTPException(status_code=400, detail="Drafting your resume needs your own Anthropic key - add it in step 1 of Build my resume.")


def status(db: Session, account) -> dict:
    mine = _saved()
    house = bool(account.is_admin and config.ANTHROPIC_API_KEY)
    granted = _house_grant(db, account)
    left = runs_left(db, account) if granted else None  # None: no limit
    return {
        "has_own_key": mine is not None,
        "last4": mine["last4"] if mine else None,
        "using_house_key": (house or granted) and mine is None,
        "usable": mine is not None or house or (granted and (left is None or left > 0)),
        "system_key": granted,  # an administrator lets this person fall back to the system key
        "system_runs_left": left,  # null: no limit
        "system_runs_limit": config.HOUSE_KEY_CALLS_PER_MONTH if granted and config.HOUSE_KEY_CALLS_PER_MONTH > 0 else None,
        "get_url": GET_KEY_URL,
    }
