"""letting particular people use the server's Anthropic key, and counting what they use

Revision ID: i6c0f3e7b9d4
Revises: h5b9e2d6a8c3
Create Date: 2026-11-20 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i6c0f3e7b9d4"
down_revision: Union[str, Sequence[str], None] = "h5b9e2d6a8c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("house_key", sa.Boolean(), nullable=False, server_default="0"))
    op.create_table(
        "house_key_uses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_house_key_uses_user_id", "house_key_uses", ["user_id"])
    op.create_index("ix_house_key_uses_at", "house_key_uses", ["at"])


def downgrade() -> None:
    op.drop_index("ix_house_key_uses_at", table_name="house_key_uses")
    op.drop_index("ix_house_key_uses_user_id", table_name="house_key_uses")
    op.drop_table("house_key_uses")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("house_key")
