import os

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
