"""push tokens remember which extension version last used them

Revision ID: l9f3c6b2e8a4
Revises: k8e2b5a9d3f7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l9f3c6b2e8a4"
down_revision: Union[str, Sequence[str], None] = "k8e2b5a9d3f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("push_tokens", schema=None) as batch_op:
        batch_op.add_column(sa.Column("extension_version", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("push_tokens", schema=None) as batch_op:
        batch_op.drop_column("extension_version")
