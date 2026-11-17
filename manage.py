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

from shared_python import owned

from app import config, crud, models
from app.database import SessionLocal

SITE_URL = "https://resume.evancooperman.com"


# This tool is the operator at the keyboard on the server (not a signed-in person), so it looks past the per-person filter on
# purpose, with `owned.unscoped()`. Anything it creates is put in the right owner's scope explicitly.


def cmd_create(args):
    db = SessionLocal()
    try:
        with owned.unscoped():
            if args.person:
                person = db.query(models.Person).filter(models.Person.slug == args.person).first()
                if person is None:
                    print(f"error: no person with slug {args.person!r}", file=sys.stderr)
                    sys.exit(1)
            else:
                person = db.query(models.Person).filter(models.Person.is_default.is_(True)).first()
                if person is None:
                    print("error: no default person is set up yet - add one via the admin UI first", file=sys.stderr)
                    sys.exit(1)
            owner_id, person_id, person_name = person.owner_id, person.id, person.name
        with owned.scope(owner_id):  # the code belongs to whoever owns the person it unlocks
            access_code = crud.generate_code(db, label=args.label, days_valid=args.days, person_id=person_id)
        # No ?person=<slug> needed - the code is tied to person.id directly
        # and app/main.py looks that up first, so it's authoritative on its
        # own regardless of who's currently the default (see
        # app/models.AccessCode's docstring).
        url = f"{SITE_URL}/?code={access_code.code}"
        print(f"Created code for: {access_code.label or '(no label)'} (person: {person_name})")
        print(f"  expires: {access_code.expires_at or 'never'}")
        print(f"  URL: {url}")
    finally:
        db.close()


def cmd_list(args):
    db = SessionLocal()
    try:
        with owned.unscoped():
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
            with owned.unscoped():
                person = db.query(models.Person).filter(models.Person.id == c.person_id).first() if c.person_id is not None else None
            person_bit = f" [{person.name}]" if person else " [no person - pre-migration?]"
            print(f"[{c.id}] {c.label or '(no label)':30s}{person_bit} {status:20s} used {c.use_count}x{last_used}")
            print(f"      {c.code}")
    finally:
        db.close()


def cmd_revoke(args):
    db = SessionLocal()
    try:
        with owned.unscoped():
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
        owner_email = crud.normalize_email(args.owner or (sorted(config.ADMIN_EMAILS)[0] if config.ADMIN_EMAILS else ""))
        with owned.unscoped():
            owner = crud.adopt_or_create_admin(db, owner_email) if owner_email in config.ADMIN_EMAILS else crud.get_user_by_email(db, owner_email)
        if owner is None:
            print(f"error: {owner_email or 'no owner'!r} has no account - let them in via the admin UI first, or pass --owner", file=sys.stderr)
            sys.exit(1)
        try:
            with owned.scope(owner.id):
                # Only an administrator's resume can ever be the site's default (and the public front page).
                person = crud.create_person(db, args.slug, args.name, resume_yaml, is_default=args.default, allow_default=owner_email in config.ADMIN_EMAILS)
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
    p_seed.add_argument("--owner", help="Email of the account that owns it (default: the first administrator)")
    p_seed.set_defaults(func=cmd_seed_person)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
