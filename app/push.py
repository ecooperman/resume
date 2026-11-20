"""The one door into the admin app that takes a personal token instead of a sign-in: the job-scoring Chrome extension pushes the postings
it captured to /api/push/jobs, and they land in the token owner's Jobs (and get scored in the background, app/scoring.py).

Why a door of its own. Everything else in the admin app is behind the sign-in gate, which wants the browser's session cookie and refuses
changes that did not come from the app's own pages (shared_python.csrf). An extension has neither: it is not on the page, and it
should not hold a login cookie. So it carries a token the person made on the Jobs page. That token authenticates ONLY these routes
(it opens nothing else), is shown once and stored hashed, and is revoked by deleting it there.

How it plugs in: admin.py puts `PushDoor` outermost, so a request for /api/push/... skips the cookie gate and the cross-site check, and
is handled by the small app below, which does its own check and then runs inside the token owner's scope (shared_python.owned), exactly
as the gate would for that person: everything is filtered to them below the routes. Any other path goes on to the normal admin app.
Because the door lives inside the admin app, it is reachable only on the admin hostnames (app/server.py), never on the public site.

CORS: the token travels in a header (never a cookie), so no page can use it unless it already holds the token, and allowing any origin
(`*`, which browsers refuse to combine with cookies) is safe and lets the extension's own pages and content scripts call this too."""
import logging
from typing import List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from shared_python import owned
from shared_python.accounts_gate import Account
from sqlalchemy.orm import Session

from . import access, apikeys, config, crud, extension, scoring
from .database import SessionLocal
from .deps import get_db

log = logging.getLogger(__name__)

PREFIX = "/api/push/"
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "authorization, content-type, x-extension-version",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Max-Age": "600",
}

app = FastAPI(title="Resume push", docs_url=None, redoc_url=None, openapi_url=None)


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    return value[7:].strip() if value[:7].lower() == "bearer " else ""


def _version(request: Request) -> Optional[str]:
    """The extension's own version, if it said (only plain dotted numbers are kept: it is a request header)."""
    value = request.headers.get("x-extension-version", "").strip()
    return value if extension.parse_version(value) else None


def _account_for_token(raw: str, version: Optional[str] = None) -> Optional[Account]:
    with owned.unscoped(), SessionLocal() as db:
        user = crud.user_for_token(db, raw, version)
        email = user.email if user is not None else None
    if email is None:
        return None
    found = access.account_for(email)  # also refuses someone whose access was removed
    return found if isinstance(found, Account) else None


def _no(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers={**CORS, "Cache-Control": "no-store"})


@app.middleware("http")
async def token_only(request: Request, call_next):
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS)
    account = await run_in_threadpool(_account_for_token, _bearer(request), _version(request))
    request.state.extension_version = _version(request)
    if account is None:
        return _no(401, "That token isn't valid. Make a new one on the Jobs page, or check it was copied whole.")
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length is None:
            return _no(411, "Send a Content-Length with the request.")
        if not length.isdigit() or int(length) > config.MAX_PUSH_BYTES:
            return _no(413, f"That is more than {config.MAX_PUSH_BYTES // (1024 * 1024)} MB; send fewer postings at a time.")
    request.state.account = account
    with owned.scope(account.id):
        response = await call_next(request)
    response.headers.update(CORS)
    response.headers["Cache-Control"] = "no-store"
    return response


def _account(request: Request) -> Account:
    return request.state.account


@app.get(PREFIX + "ping")
def ping(request: Request, account: Account = Depends(_account)):
    """Lets the extension check a token and say whose Jobs it is connected to, and whether a newer version of the extension is out."""
    return {"ok": True, "email": account.email, "extension": extension.update_info(request.state.extension_version)}


class PushedJob(BaseModel):
    url: str
    title: Optional[str] = None
    company: Optional[str] = None
    salary: Optional[str] = None
    jd_text: Optional[str] = None
    # a posting the extension already scored itself (an older extension, or an import): kept as it is
    tier: Optional[str] = None
    summary: Optional[str] = None
    matched: Optional[str] = None
    gaps: Optional[str] = None
    strengths: Optional[str] = None


class PushIn(BaseModel):
    jobs: List[PushedJob]
    person_id: Optional[int] = None  # which of their resumes these are for (default: their default, or only, one)
    score: bool = True  # False: just save them


@app.post(PREFIX + "jobs")
def push_jobs(request: Request, body: PushIn, account: Account = Depends(_account), db: Session = Depends(get_db)):
    """Save the postings, and queue the ones with a description and no score for scoring. Safe to send the same posting again: it is
    matched by link and only ever fills in what was missing."""
    if len(body.jobs) > config.MAX_PUSH_ITEMS:
        return _no(400, f"Send at most {config.MAX_PUSH_ITEMS} postings at a time.")
    try:
        result = crud.push_jobs(db, [j.model_dump() for j in body.jobs], body.person_id)
    except crud.JobError as e:
        return _no(400, str(e))
    to_score = result.pop("to_score")
    out = {**result, "queued": 0, "scoring_blocked": None}
    if body.score and to_score:
        blocked = scoring.blocked_reason(db, account)
        if blocked:
            out["scoring_blocked"] = blocked  # they are saved; scoring starts when the person has what it needs and presses the button
        else:
            out["queued"] = scoring.enqueue(account, to_score)
    out["scoring"] = scoring.status(account.id)
    out["extension"] = extension.update_info(request.state.extension_version)
    return out


class PushDoor:
    """ASGI middleware, outermost in the admin app: hands /api/push/... to the token-only app above, everything else straight on."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(PREFIX):
            await app(scope, receive, send)
        else:
            await self.app(scope, receive, send)
