"""each person's stored source text (LinkedIn career files, old resumes) and their unsaved draft

Revision ID: f2d6c0b8e4a5
Revises: e1c5b9a7d4f3
Create Date: 2026-11-18 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2d6c0b8e4a5"
down_revision: Union[str, Sequence[str], None] = "e1c5b9a7d4f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="document"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("found", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.String(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_files_owner_id", "source_files", ["owner_id"])
    op.create_table(
        "builder_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("resume_yaml", sa.Text(), nullable=False, server_default=""),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="uq_builder_drafts_owner"),
    )
    op.create_index("ix_builder_drafts_owner_id", "builder_drafts", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_builder_drafts_owner_id", table_name="builder_drafts")
    op.drop_table("builder_drafts")
    op.drop_index("ix_source_files_owner_id", table_name="source_files")
    op.drop_table("source_files")
