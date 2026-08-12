#!/usr/bin/env python3
"""
Single build step: resume.yaml -> two parallel trees, each with an
index.html, resume.pdf, and resume.docx:

    site/private/   full data, including contact info
    site/public/    email/phone/location stripped out at build time

    python build.py

app/main.py decides which tree to serve per-request based on whether a
valid ?code=... access code was given (see app/crud.py) -- this script only
ever produces pre-built static files, no PDF/DOCX generation happens
per-request.

The HTML page is the actual design; the PDF is that same page printed by a
headless browser (so it's always visually in sync with the web version with
zero extra styling work). The DOCX is generated separately with python-docx
since Word can't just render CSS -- it's styled to look clean, not to be a
pixel copy of the HTML.

The actual rendering functions (render_html/build_pdf/build_docx/load_data)
live in app/render.py, not here -- app/admin.py's /api/render endpoint reuses
them too, to turn a *tailored* resume (from the time-management app) into
PDF/DOCX bytes without touching this file or site/ at all.
"""
import copy
import sys
from pathlib import Path

from app.render import build_docx, build_pdf, load_data, render_html

ROOT = Path(__file__).parent
SITE = ROOT / "site"

REDACTED_BASICS_FIELDS = ("email", "phone", "location")


def redact(data):
    """Deep-copy data with contact fields stripped from basics, for the
    public/ tree that's served without a valid access code."""
    redacted = copy.deepcopy(data)
    for field in REDACTED_BASICS_FIELDS:
        redacted["basics"].pop(field, None)
    return redacted


def build_html(data, out_dir):
    html = render_html(data, show_download_bar=True)
    out = out_dir / "index.html"
    out.write_text(html)
    print(f"wrote {out}")


def build_tree(data, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    build_html(data, out_dir)
    build_pdf(data, out_dir)
    build_docx(data, out_dir)


def main():
    data = load_data()
    build_tree(data, SITE / "private")
    build_tree(redact(data), SITE / "public")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
