"""Both resume sites in ONE process: the public resume (app/main.py, no sign-in) and the signed-in admin app (app/admin.py).

They used to be two services (ports 8040 and 8041) in one repo sharing one database. The split between them is real and stays: the public
site shows only published resumes and never asks anyone to sign in, while the admin app is default-deny and has the builder, people, codes
and users. What is not worth having is two of everything to run, restart and deploy. So this module hands each request to one app or the
other by its Host header: the hostnames in config.ADMIN_HOSTS get the admin app, every other host (the public hostnames, an IP, anything
unknown) gets the public site. An unknown host landing on the PUBLIC app is the safe direction: it has no sign-in-only routes at all.

Nothing about either app changes: each keeps its own routes, middleware and sign-in gate, so the admin app is exactly as locked down
as when it had its own port. `uvicorn app.main:app` and `uvicorn app.admin:app` still work on their own (local development, tests)."""
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from . import admin, config, main


def request_host(scope) -> str:
    """The hostname the request was addressed to: lowercase, no port, no trailing dot ("Resume-Admin.Example.com:443" -> "resume-admin.example.com")."""
    for name, value in scope.get("headers") or []:
        if name == b"host":
            host = value.decode("latin-1").strip().lower()
            if host.startswith("["):  # an IPv6 literal: [::1]:8040
                return host[1:].split("]", 1)[0]
            return host.split(":", 1)[0].rstrip(".")
    return ""


@asynccontextmanager
async def _both_lifespans(_app):
    """Start and stop what each app starts and stops (the sign-in key refresh, for one), as if each ran alone."""
    async with AsyncExitStack() as stack:
        for sub in (main.app, admin.app):
            await stack.enter_async_context(sub.router.lifespan_context(sub))
        yield


_root = FastAPI(lifespan=_both_lifespans)  # serves nothing itself: it only carries the lifespan for the two apps below


async def app(scope, receive, send):
    """The ASGI entry point (`uvicorn app.server:app`)."""
    if scope["type"] in ("http", "websocket"):
        target = admin.app if request_host(scope) in config.ADMIN_HOSTS else main.app
        await target(scope, receive, send)
    else:
        await _root(scope, receive, send)
