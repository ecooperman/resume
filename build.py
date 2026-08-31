#!/usr/bin/env python3
"""
Single build step: every person (see app/models.Person) -> two parallel
trees each, keyed by slug:

    site/private/<slug>/   full data, including contact info
    site/public/<slug>/    email/phone/location stripped out at build time

    python build.py

app/main.py resolves which person's tree to serve per-request: a valid
?code=... is looked up first and is authoritative on its own (a code is
tied to exactly one person, see app/models.AccessCode); otherwise it falls
back to ?person=<slug> (or whoever's currently the default person if that's
also absent) for public browsing -- this script only ever produces
pre-built static files, no PDF/DOCX generation happens per-request.

The HTML page is the actual design; the PDF is that same page printed by a
headless browser (so it's always visually in sync with the web version with
zero extra styling work). The DOCX is generated separately with python-docx
since Word can't just render CSS -- it's styled to look clean, not to be a
pixel copy of the HTML.

The actual rendering (render_html/build_pdf/build_docx/build_tree/redact)
lives in app/render.py, not here - this file is just the CLI entry point
that loops over every person. app/admin.py's People routes rebuild a single
person's tree directly too (after any change that could affect the live
site - editing their resume, or switching who's default just by writing
into a slug-keyed path that already exists), so an admin-UI edit takes
effect immediately rather than waiting for the next deploy. app/admin.py's
/api/render endpoint separately reuses build_pdf/build_docx alone, to turn
a *tailored* resume (from the time-management app) into ad-hoc PDF/DOCX
bytes without touching site/ at all.
"""
import sys
from pathlib import Path

import yaml

from app.crud import list_people
from app.database import SessionLocal
from app.render import build_tree, redact

ROOT = Path(__file__).parent
SITE = ROOT / "site"


def build_person(person) -> None:
    data = yaml.safe_load(person.resume_yaml)
    build_tree(data, SITE / "private" / person.slug)
    build_tree(redact(data), SITE / "public" / person.slug)


def main():
    db = SessionLocal()
    try:
        people = list_people(db)
    finally:
        db.close()
    if not people:
        # Not a fatal error: deploy.yml runs this unconditionally on every
        # push, including the very first deploy of the People feature
        # itself, when the freshly-migrated people table is still empty
        # (nobody's been added through the admin UI yet). Failing hard here
        # would abort deploy.yml's whole script (set -e) before it ever
        # reaches the systemctl restarts that bring up the admin UI you'd
        # use to fix this - a real bootstrapping deadlock. Skipping the
        # build leaves site/ as whatever it last successfully was (or
        # empty, if truly never built before); nothing else about the
        # deploy is affected.
        print(
            "warning: no people are set up yet - skipping the site build. "
            "Add someone via the admin UI (People) and re-run `python "
            "build.py` by hand, or just push again once one exists.",
            file=sys.stderr,
        )
        return
    for person in people:
        build_person(person)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
