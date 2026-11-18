"""A person's own Anthropic key, which drafts their resume from the documents they give it.

Each person brings their own key, so nobody is metered on the owner's bill. Which key a draft uses:

1. the signed-in person's own saved key, if they have one;
2. otherwise the server's own key from the environment, but ONLY for an administrator. Anyone else without a key of their
   own simply cannot draft, and is told how to turn it on.

Saved keys are encrypted (shared_python.keystore: KEY_ENCRYPTION_SECRET), never sent back to the browser (only the last four
characters are kept in the clear) and never logged. This module is the same idea as trip-planning's apikeys.py, for the one
provider this app calls."""
import logging
from typing import Optional, Tuple

import requests
from fastapi import HTTPException
from shared_python import keystore
from shared_python.keystore import KeyStorageNotConfigured, clean, scrub, storage_available  # noqa: F401 (the routes use these)
from sqlalchemy.orm import Session

from . import config, models

log = logging.getLogger(__name__)

PROVIDER = "anthropic"
TIMEOUT = 10
GET_KEY_URL = "https://console.anthropic.com/settings/keys"


def _row(db: Session) -> Optional[models.UserApiKey]:
    return db.query(models.UserApiKey).filter_by(provider=PROVIDER).first()  # only the signed-in person's own (shared_python.owned)


def save(db: Session, raw_key: str) -> models.UserApiKey:
    key = clean(raw_key)
    token = keystore.encrypt(key)
    row = _row(db)
    if row is None:
        row = models.UserApiKey(provider=PROVIDER, encrypted_key=token, last4=keystore.last4(key))
        db.add(row)
    else:
        row.encrypted_key, row.last4 = token, keystore.last4(key)
    db.commit()
    db.refresh(row)
    return row


def remove(db: Session) -> bool:
    row = _row(db)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def resolve(db: Session, is_admin: bool) -> Optional[str]:
    """The key to use for this person, or None."""
    row = _row(db)
    if row is not None:
        own = keystore.decrypt(row.encrypted_key, PROVIDER)
        if own:
            return own
    if is_admin:
        return config.ANTHROPIC_API_KEY or None
    return None


def require(db: Session, is_admin: bool) -> str:
    key = resolve(db, is_admin)
    if key:
        return key
    raise HTTPException(status_code=400, detail="Drafting your resume needs your own Anthropic key - add it in step 1 of Build my resume.")


def status(db: Session, is_admin: bool) -> dict:
    row = _row(db)
    readable = row is not None and keystore.decrypt(row.encrypted_key, PROVIDER) is not None
    house = bool(is_admin and config.ANTHROPIC_API_KEY)
    return {
        "can_save": storage_available(),
        "has_own_key": row is not None,
        "own_key_readable": readable if row is not None else True,
        "last4": row.last4 if row is not None else None,
        "using_house_key": house and not readable,
        "usable": readable or house,
        "get_url": GET_KEY_URL,
    }


def verify(key: str) -> Tuple[Optional[bool], str]:
    """One tiny real call so a typo is caught while the person is still on the page. (True, "") if Anthropic accepted it,
    (False, why) if it refused it, (None, why) if we could not tell (then the key is saved and the person is told it was not
    checked)."""
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/models",
            params={"limit": 1},
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=TIMEOUT,
        )
    except (requests.RequestException, ValueError) as exc:
        log.warning("Checking an Anthropic key failed: %s", type(exc).__name__)  # the type only: a message can echo the URL
        return None, "Couldn't reach Anthropic to check the key"
    if r.status_code in (401, 403):
        return False, "Anthropic didn't accept that key"
    if r.ok:
        return True, ""
    return None, f"Anthropic answered with an error ({r.status_code})"
