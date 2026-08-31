#!/usr/bin/env python3
"""
Manage access codes that unlock the full resume (contact info + unredacted
downloads) at resume.evancooperman.com/?code=<code>. Each code is scoped to
one specific person (see app/models.AccessCode) - --person defaults to
whoever's currently the default person, and only needs to be passed
explicitly for someone else (whose URL then also needs ?person=<slug>, see
app/main.py's _resolve_dir - printed automatically by `create` below).

    python manage.py create --label "Acme Corp recruiter" --days 30
    python manage.py create --label "Rach's recruiter" --person rach
    python manage.py list
    python manage.py revoke <code-or-id>

Also has a one-time bootstrap helper for loading an existing resume yaml
*file* into the people table as a real Person row (see app/models.py) --
useful once, to seed the first person or two from an existing file; every
edit after that goes through the admin UI (People), never a file again:

    python manage.py seed-person --slug evan --name "Evan Cooperman" \\
        --file evan-resume.yaml --default

Run this against whichever access.db actually backs the live site -- on the
droplet, that means running it from /opt/apps/resume with the venv active,
same as any other DB-touching command there. A code only works against the
database it was created in.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from app import crud
from app.database import SessionLocal

SITE_URL = "https://resume.evancooperman.com"


def cmd_create(args):
    db = SessionLocal()
    try:
        if args.person:
            person = crud.get_person_by_slug(db, args.person)
            if person is None:
                print(f"error: no person with slug {args.person!r}", file=sys.stderr)
                sys.exit(1)
        else:
            person = crud.get_default_person(db)
            if person is None:
                print("error: no default person is set up yet - add one via the admin UI first", file=sys.stderr)
                sys.exit(1)
        access_code = crud.generate_code(db, label=args.label, days_valid=args.days, person_id=person.id)
        # No ?person=<slug> needed - the code is tied to person.id directly
        # and app/main.py looks that up first, so it's authoritative on its
        # own regardless of who's currently the default (see
        # app/models.AccessCode's docstring).
        url = f"{SITE_URL}/?code={access_code.code}"
        print(f"Created code for: {access_code.label or '(no label)'} (person: {person.name})")
        print(f"  expires: {access_code.expires_at or 'never'}")
        print(f"  URL: {url}")
    finally:
        db.close()


def cmd_list(args):
    db = SessionLocal()
    try:
        codes = crud.list_codes(db)
        if not codes:
            print("No codes yet.")
            return
        for c in codes:
            if c.revoked_at:
                status = f"revoked {c.revoked_at:%Y-%m-%d}"
            elif c.expires_at and c.expires_at < datetime.utcnow():
                status = f"expired {c.expires_at:%Y-%m-%d}"
            else:
                status = "active"
            last_used = f", last used {c.last_used_at:%Y-%m-%d}" if c.last_used_at else ""
            person = crud.get_person(db, c.person_id) if c.person_id is not None else None
            person_bit = f" [{person.name}]" if person else " [no person - pre-migration?]"
            print(f"[{c.id}] {c.label or '(no label)':30s}{person_bit} {status:20s} used {c.use_count}x{last_used}")
            print(f"      {c.code}")
    finally:
        db.close()


def cmd_revoke(args):
    db = SessionLocal()
    try:
        row = crud.revoke_code(db, args.code_or_id)
        if row is None:
            print(f"No active code matching '{args.code_or_id}' found.", file=sys.stderr)
            sys.exit(1)
        print(f"Revoked: {row.label or '(no label)'} [{row.id}]")
    finally:
        db.close()


def cmd_seed_person(args):
    db = SessionLocal()
    try:
        resume_yaml = Path(args.file).read_text()
        try:
            person = crud.create_person(db, args.slug, args.name, resume_yaml, is_default=args.default)
        except crud.PersonError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Created person '{person.name}' (slug={person.slug}, id={person.id}, default={person.is_default})")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Generate a new access code")
    p_create.add_argument("--label", help="Who this code is for, e.g. 'Acme Corp recruiter'")
    p_create.add_argument("--days", type=int, default=None, help="Days until it expires (default: never)")
    p_create.add_argument(
        "--person", help="Slug of the person this code unlocks (default: the current default person)"
    )
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="List all codes and their status")
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser("revoke", help="Revoke a code by its code string or id")
    p_revoke.add_argument("code_or_id")
    p_revoke.set_defaults(func=cmd_revoke)

    p_seed = sub.add_parser("seed-person", help="One-time bootstrap: load an existing resume yaml file as a Person")
    p_seed.add_argument("--slug", required=True, help='Stable identifier, e.g. "evan" - lowercase, numbers, hyphens')
    p_seed.add_argument("--name", required=True, help='Display name, e.g. "Evan Cooperman"')
    p_seed.add_argument("--file", required=True, help="Path to the existing resume yaml file to load")
    p_seed.add_argument("--default", action="store_true", help="Make this the default person")
    p_seed.set_defaults(func=cmd_seed_person)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
