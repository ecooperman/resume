import os
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8040

# "local" (default, for a dev machine) or "production" (set once, droplet-wide, via
# systemd's DefaultEnvironment - see DEPLOYMENT.md). Picks which shared-assets host the
# admin pages load their theme from, so local iteration never means hand-editing a URL.
ENV = os.environ.get("APP_ENV", "local")
SHARED_ASSETS_BASE = "https://static.evancooperman.com" if ENV == "production" else "http://127.0.0.1:8070"
# The resume's public pages and cover letters are rendered once into static files (and PDFs), so they must never carry the local host:
# their tab icon always points at production, whatever APP_ENV was when they were built.
PUBLIC_ASSETS_BASE = "https://static.evancooperman.com"

# Resume-generation feature (see app/generate.py + admin_static's "Generate
# a resume.yaml" section): calls the Anthropic API to synthesize a
# resume.yaml from uploaded documents/a LinkedIn export. Unset locally?
# The feature degrades to a clear 503 (not a crash) - see generate.py.
# On the droplet it comes from the shared secrets file every app reads
# (/etc/personal-apps/shared.env, see DEPLOYMENT.md) - never committed;
# export it in your shell for local dev.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# --- accounts (see app/access.py) ---------------------------------------------------------------------------------------
# Only people with an account get into the admin app; each sees only their own resume. Locally nobody signs in, so requests
# count as coming from DEV_USER_EMAIL (an administrator); never used in production. ADMIN_EMAILS (comma-separated) comes from
# the shared secrets file every app reads and says who may manage people, codes and publishing, and use the server's
# Anthropic key.
PORT_ADMIN = 8041
DEV_USER_EMAIL = os.environ.get("DEV_USER_EMAIL", "dev@localhost.test").strip().lower()
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
if ENV != "production":
    ADMIN_EMAILS.add(DEV_USER_EMAIL)

# app/server.py runs BOTH sites in one process and tells them apart by the Host header: these hostnames (comma-separated, lowercase, no port)
# get the signed-in admin app, everything else gets the public site. jobs.evancooperman.com is the app's address (renamed from resume-admin on
# 2026-11-20); the old resume-admin address stays as an ALIAS so bookmarks, emails already sent and Chrome extensions installed with it keep
# working (retire it later, see TODO.md). The .localhost names are for the other apps' calls on this machine (the dashboard reaches the admin app at
# http://resume-admin.localhost:8040) and for local development: one `uvicorn app.server:app` serves the public site at localhost:8040 and the
# admin app at jobs.localhost:8040 (or resume-admin.localhost:8040). The PUBLIC hostname (resume.evancooperman.com) must never be in this list.
DEFAULT_ADMIN_HOSTS = ("jobs.evancooperman.com", "resume-admin.evancooperman.com", "jobs.localhost", "resume-admin.localhost")
ADMIN_HOSTS = {h.strip().lower() for h in os.environ.get("RESUME_ADMIN_HOSTS", ",".join(DEFAULT_ADMIN_HOSTS)).split(",") if h.strip()}
# Old admin addresses that should send people to the new one: in production a browser VISIT to one of these (GET or HEAD, anything but /api/...) is redirected to
# ADMIN_PUBLIC_URL, so old bookmarks, emails and tabs move over. Their /api/ calls are NOT redirected and still work, because Chrome extensions installed before 0.3.2
# send their postings to the old address. (Cloudflare Access must let the visit reach the app for this to happen: see DEPLOYMENT.md "The jobs hostname".)
LEGACY_ADMIN_HOSTS = {h.strip().lower() for h in os.environ.get("RESUME_LEGACY_HOSTS", "resume-admin.evancooperman.com").split(",") if h.strip()}

# The server's own Anthropic key (above) is for administrators. An administrator may also let particular people fall back to it when they
# have no key of their own (the Users page). By default that is unlimited; set HOUSE_KEY_CALLS_PER_MONTH to give each of them that many AI runs a
# calendar month (a draft, a pasted-list import and a review each count one), after which they need a key of their own. Every run is counted
# either way, for the usage line on the Users page; the prices are only for its estimate (dollars per million tokens for the model in app/generate.py).
HOUSE_KEY_CALLS_PER_MONTH = int(os.environ.get("HOUSE_KEY_CALLS_PER_MONTH", "0"))  # 0 = no limit
PRICE_INPUT_PER_MTOK = float(os.environ.get("PRICE_INPUT_PER_MTOK", "5.0"))
PRICE_OUTPUT_PER_MTOK = float(os.environ.get("PRICE_OUTPUT_PER_MTOK", "25.0"))  # change both with RESUME_MODEL: they are per million tokens of THAT model


def bounded_int(raw, default: int, low: int, high: int) -> int:
    """A whole-number setting from the environment: the default when it is missing or not a number, and never outside [low, high]."""
    try:
        return max(low, min(high, int(str(raw).replace("_", "").replace(",", "").strip())))
    except (TypeError, ValueError):
        return default


# The model every AI step uses (drafting, the pasted-list import, the review, scoring a job, tailoring) and the most tokens a draft or a tailored
# resume may use for thinking plus the answer. Settings so either can change on the droplet without a code change: RESUME_MODEL (default
# claude-opus-5) and RESUME_DRAFT_MAX_TOKENS (default 20,000; kept between 4,000 and 21,000, because a request that is not streamed may not ask for
# more than the SDK's 21,333-token ceiling). Raise the limit if a long career is cut off ("never became valid"). A model with other prices needs
# PRICE_INPUT_PER_MTOK / PRICE_OUTPUT_PER_MTOK changed to match, or the Users page's cost estimate is off.
ANTHROPIC_MODEL = (os.environ.get("RESUME_MODEL") or "claude-opus-5").strip()
DRAFT_MAX_TOKENS = bounded_int(os.environ.get("RESUME_DRAFT_MAX_TOKENS"), default=20_000, low=4_000, high=21_000)

# Jobs (app/jobs.py): how many one account may keep, and the largest CSV import (rows and bytes). A job description is cut at MAX_JD_CHARS.
MAX_JOBS_PER_ACCOUNT = 2000
MAX_IMPORT_ROWS = 1000
MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_JD_CHARS = 60_000
SCORES_PER_HOUR = 200  # scoring a job is one AI run; this keeps a stuck page or script from running up the bill, and still lets a long list through
TAILORS_PER_HOUR = 30  # tailoring a resume and letter is a long, costly AI run (a draft's size); one at a time per person, and no more than this an hour

# The installable zip of the job-scoring Chrome extension (built from its own repo with its package.py) lives in this folder; the install page serves the newest.
EXTENSION_DIR = Path(__file__).parent.parent / "extension"

# The Chrome extension pushes jobs with a personal token (app/push.py): how many tokens one account may hold, how many postings and bytes one push may carry.
PUSH_TOKEN_PREFIX = "rjt_"
MAX_TOKENS_PER_ACCOUNT = 10
MAX_PUSH_ITEMS = 200
MAX_PUSH_BYTES = 12 * 1024 * 1024

# How many different people an ordinary account may ask to have let in (administrators: no limit). Each email counts once, whatever
# became of the request, until the person withdraws one still waiting, so being refused does not hand the slot back.
INVITES_PER_USER = int(os.environ.get("INVITES_PER_USER", "5"))

# Where emails send people to sign in and where the public site lives.
ADMIN_PUBLIC_URL = "https://jobs.evancooperman.com" if ENV == "production" else f"http://127.0.0.1:{PORT_ADMIN}"
PUBLIC_SITE_URL = "https://resume.evancooperman.com" if ENV == "production" else f"http://127.0.0.1:{PORT}"

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
