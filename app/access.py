"""Who may use the admin app, and whose resumes they see.

Several people can have an account, each with a completely private resume. The app has no account for anyone until
they are let in (default-deny): an administrator adds a person on the Users page (or the dashboard's People page), or, for
an administrator, the account is made on their first sign-in (taking over the resumes that existed before accounts).

shared_python.accounts_gate wraps the WHOLE admin app: every page, API route and file refuses anyone without an account, and
runs each request inside that person's scope, so every database read and write is filtered to them below the routes' own
code (shared_python.owned). Only an administrator may manage access codes, publish a resume, choose the site's default
person, or use the server's Anthropic key (app/admin.py).

This is the ADMIN app (port 8041). The public resume site (app/main.py, port 8040) has no sign-in on purpose and only ever shows
a resume an administrator has published."""
import re
from typing import Union

from shared_python import accounts_gate, usage
from shared_python.accounts_gate import Account

from . import config, crud, models
from .database import SessionLocal

PUBLIC_PATHS = ()  # nothing in the admin app is public
seen = usage.LastSeen(SessionLocal, app="resume")  # when each person was last here, for the dashboard's stats pages


def account_for(email: str) -> Union[Account, str]:
    """This person's account: administrators always have one (made on first sign-in), anyone else only if an administrator let
    them in and has not since removed them."""
    is_admin = email in config.ADMIN_EMAILS
    with SessionLocal() as db:
        user = crud.get_user_by_email(db, email)
        if user is None:
            if not is_admin:
                return "none"
            user = crud.adopt_or_create_admin(db, email)
        if user.status != models.USER_ACTIVE and not is_admin:
            return "removed"
        return Account(id=user.id, email=user.email, is_admin=is_admin)


def install(app) -> None:
    accounts_gate.install(
        app,
        account_for=account_for,
        dev_user_email=config.DEV_USER_EMAIL,
        public_paths=PUBLIC_PATHS,
        seen=seen,
    )
