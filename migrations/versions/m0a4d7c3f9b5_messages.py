"""messages: what people send the administrator from the Send feedback page

Revision ID: m0a4d7c3f9b5
Revises: l9f3c6b2e8a4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m0a4d7c3f9b5"
down_revision: Union[str, Sequence[str], None] = "l9f3c6b2e8a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("page", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_user_id"), "messages", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_user_id"), table_name="messages")
    op.drop_table("messages")
