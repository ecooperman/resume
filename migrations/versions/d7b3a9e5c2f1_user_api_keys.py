"""each person's own API key for the AI that drafts their resume

Revision ID: d7b3a9e5c2f1
Revises: c6a2f8d4b1e7
Create Date: 2026-11-18 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7b3a9e5c2f1"
down_revision: Union[str, Sequence[str], None] = "c6a2f8d4b1e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "provider", name="uq_user_api_keys_owner_provider"),
    )
    op.create_index("ix_user_api_keys_owner_id", "user_api_keys", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_user_api_keys_owner_id", table_name="user_api_keys")
    op.drop_table("user_api_keys")
