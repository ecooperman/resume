"""push_tokens: personal tokens the Chrome extension uses to push jobs into the app

Revision ID: k8e2b5a9d3f7
Revises: j7d1a4f8c2e6
Create Date: 2026-11-20 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k8e2b5a9d3f7"
down_revision: Union[str, Sequence[str], None] = "j7d1a4f8c2e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_push_tokens_owner_id", "push_tokens", ["owner_id"])
    op.create_index("ix_push_tokens_token_hash", "push_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_push_tokens_token_hash", table_name="push_tokens")
    op.drop_index("ix_push_tokens_owner_id", table_name="push_tokens")
    op.drop_table("push_tokens")
