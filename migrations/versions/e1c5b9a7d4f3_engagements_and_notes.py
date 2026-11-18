"""each person's engagements and builder notes, kept so later drafts can use them

Revision ID: e1c5b9a7d4f3
Revises: d7b3a9e5c2f1
Create Date: 2026-11-18 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1c5b9a7d4f3"
down_revision: Union[str, Sequence[str], None] = "d7b3a9e5c2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engagements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("employer", sa.String(), nullable=False, server_default=""),
        sa.Column("client", sa.String(), nullable=False),
        sa.Column("client_does", sa.String(), nullable=False, server_default=""),
        sa.Column("industry", sa.String(), nullable=False, server_default=""),
        sa.Column("regulated", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("regulators", sa.String(), nullable=False, server_default=""),
        sa.Column("technologies", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("role", sa.String(), nullable=False, server_default=""),
        sa.Column("start", sa.String(), nullable=False, server_default=""),
        sa.Column("end", sa.String(), nullable=False, server_default=""),
        sa.Column("outcomes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confidential", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engagements_owner_id", "engagements", ["owner_id"])
    op.create_table(
        "builder_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="uq_builder_notes_owner"),
    )
    op.create_index("ix_builder_notes_owner_id", "builder_notes", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_builder_notes_owner_id", table_name="builder_notes")
    op.drop_table("builder_notes")
    op.drop_index("ix_engagements_owner_id", table_name="engagements")
    op.drop_table("engagements")
