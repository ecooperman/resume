#!/usr/bin/env python3
"""
Manage access codes that unlock the full resume (contact info + unredacted
downloads) at resume.evancooperman.com/?code=<code>.

    python manage.py create --label "Acme Corp recruiter" --days 30
    python manage.py list
    python manage.py revoke <code-or-id>

Run this against whichever access.db actually backs the live site -- on the
droplet, that means running it from /opt/apps/resume with the venv active,
same as any other DB-touching command there. A code only works against the
database it was created in.
"""
import argparse
import sys
from datetime import datetime

from app import crud
from app.database import SessionLocal

SITE_URL = "https://resume.evancooperman.com"


def cmd_create(args):
    db = SessionLocal()
    try:
        access_code = crud.generate_code(db, label=args.label, days_valid=args.days)
        print(f"Created code for: {access_code.label or '(no label)'}")
        print(f"  expires: {access_code.expires_at or 'never'}")
        print(f"  URL: {SITE_URL}/?code={access_code.code}")
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
            print(f"[{c.id}] {c.label or '(no label)':30s} {status:20s} used {c.use_count}x{last_used}")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Generate a new access code")
    p_create.add_argument("--label", help="Who this code is for, e.g. 'Acme Corp recruiter'")
    p_create.add_argument("--days", type=int, default=None, help="Days until it expires (default: never)")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="List all codes and their status")
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser("revoke", help="Revoke a code by its code string or id")
    p_revoke.add_argument("code_or_id")
    p_revoke.set_defaults(func=cmd_revoke)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
