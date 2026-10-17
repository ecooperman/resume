import os

HOST = "127.0.0.1"
PORT = 8040

# Resume-generation feature (see app/generate.py + admin_static's "Generate
# a resume.yaml" section): calls the Anthropic API to synthesize a
# resume.yaml from uploaded documents/a LinkedIn export. Unset locally?
# The feature degrades to a clear 503 (not a crash) - see generate.py.
# Set on the droplet's systemd unit (never committed); export in your shell
# for local dev. Same variable name/convention as time-management's own
# ANTHROPIC_API_KEY (see that repo's app/config.py) - a separate key is
# fine too, they're unrelated processes.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
