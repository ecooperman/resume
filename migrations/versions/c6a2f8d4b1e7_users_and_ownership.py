"""users, an owner on every resume and code, and a published flag

Until now the admin was private to its one administrator and nothing had an owner. Now several people can have
accounts, each with their own completely private resume: `users`, an owner on `people` and `access_codes`, and
`people.published` (only a published person can be seen on the public site).

Everything that exists now is parked on a placeholder user (legacy-owner@resume.invalid); the first administrator to sign
in takes the placeholder over by giving it their email (app/crud.py adopt_or_create_admin), so it all becomes theirs without
moving a row. Every existing person is published, because they were all publicly visible before; every new one is not.

Revision ID: c6a2f8d4b1e7
Revises: ad0a7ca7e069
Create Date: 2026-11-17 18:00:00.000000

"""
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6a2f8d4b1e7"
down_revision: Union[str, Sequence[str], None] = "ad0a7ca7e069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_OWNER_EMAIL = "legacy-owner@resume.invalid"
OWNED = ("people", "access_codes")
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text("INSERT INTO users (email, status, created_at) VALUES (:email, 'active', :now)"),
        {"email": LEGACY_OWNER_EMAIL, "now": datetime.utcnow()},
    )
    legacy_id = conn.execute(sa.text("SELECT id FROM users WHERE email = :e"), {"e": LEGACY_OWNER_EMAIL}).scalar()

    with op.batch_alter_table("people", naming_convention=NAMING) as batch_op:
        batch_op.add_column(sa.Column("published", sa.Boolean(), nullable=False, server_default="0"))
    conn.execute(sa.text("UPDATE people SET published = 1"))  # they were all public before accounts

    for table in OWNED:
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(f"fk_{table}_owner_id_users", "users", ["owner_id"], ["id"])
    conn.execute(sa.text("UPDATE people SET owner_id = :owner"), {"owner": legacy_id})
    conn.execute(
        sa.text("UPDATE access_codes SET owner_id = COALESCE((SELECT owner_id FROM people WHERE people.id = access_codes.person_id), :owner)"),
        {"owner": legacy_id},
    )
    for table in OWNED:
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_index(f"ix_{table}_owner_id", ["owner_id"])


def downgrade() -> None:
    """Downgrade schema: everyone's resumes and codes end up in one place again, and who owned what is forgotten."""
    for table in OWNED:
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.drop_index(f"ix_{table}_owner_id")
            batch_op.drop_constraint(f"fk_{table}_owner_id_users", type_="foreignkey")
            batch_op.drop_column("owner_id")
    with op.batch_alter_table("people", naming_convention=NAMING) as batch_op:
        batch_op.drop_column("published")
    op.drop_table("users")
