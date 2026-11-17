import os
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8040

# "local" (default, for a dev machine) or "production" (set once, droplet-wide, via
# systemd's DefaultEnvironment - see DEPLOYMENT.md). Picks which shared-assets host the
# admin pages load their theme from, so local iteration never means hand-editing a URL.
ENV = os.environ.get("APP_ENV", "local")
SHARED_ASSETS_BASE = "https://static.evancooperman.com" if ENV == "production" else "http://127.0.0.1:8070"

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

# Where emails send people to sign in and where the public site lives.
ADMIN_PUBLIC_URL = "https://resume-admin.evancooperman.com" if ENV == "production" else f"http://127.0.0.1:{PORT_ADMIN}"
PUBLIC_SITE_URL = "https://resume.evancooperman.com" if ENV == "production" else f"http://127.0.0.1:{PORT}"

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
