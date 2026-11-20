"""Telling people things - the sending lives in shared_python.notify; this is where Resume's own wording and links go."""
from shared_python import notify as shared
from shared_python.notify import describe_failure, notify, send_email  # noqa: F401 - re-exported

from . import config

APP_NAME = "Resume"


def invite_requested(inviter_email: str, invitee_email: str) -> None:
    """Tell the administrator that someone asked for a person to be let in (it joins the periodic digest, see shared_python.notify)."""
    shared.invite_requested(APP_NAME, inviter_email, invitee_email, None, f"{config.ADMIN_PUBLIC_URL}/invites.html")


def tell_let_in(to: str, inviter_email: str) -> None:
    """Email someone that an administrator has let them into the app, with a link that opens account creation."""
    shared.tell_let_in(to, inviter_email, APP_NAME, config.ADMIN_PUBLIC_URL)
