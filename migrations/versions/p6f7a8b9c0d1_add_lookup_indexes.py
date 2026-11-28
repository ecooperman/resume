"""indexes on the columns the app looks rows up by (foreign keys and a date), so a lookup no longer reads the whole table

Revision ID: p6f7a8b9c0d1
Revises: n1b5e8d4a0c6
"""
from typing import Sequence, Union

from alembic import op

revision: str = "p6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "n1b5e8d4a0c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_jobs_person_id", "jobs", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_person_id", table_name="jobs")
