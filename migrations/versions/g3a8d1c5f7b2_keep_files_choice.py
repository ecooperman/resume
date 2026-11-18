"""each person's choice of whether the builder keeps their files and drafts (on by default)

Revision ID: g3a8d1c5f7b2
Revises: f2d6c0b8e4a5
Create Date: 2026-11-18 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g3a8d1c5f7b2"
down_revision: Union[str, Sequence[str], None] = "f2d6c0b8e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("builder_notes") as batch:
        batch.add_column(sa.Column("keep_files", sa.Boolean(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("builder_notes") as batch:
        batch.drop_column("keep_files")
