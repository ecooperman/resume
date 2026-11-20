"""jobs: a person's tracked jobs, their scores and the resume and cover letter made for each

Revision ID: j7d1a4f8c2e6
Revises: i6c0f3e7b9d4
Create Date: 2026-11-20 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j7d1a4f8c2e6"
down_revision: Union[str, Sequence[str], None] = "i6c0f3e7b9d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("company_url", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("salary", sa.String(), nullable=True),
        sa.Column("tier", sa.String(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("matched", sa.Text(), nullable=True),
        sa.Column("gaps", sa.Text(), nullable=True),
        sa.Column("strengths", sa.Text(), nullable=True),
        sa.Column("scored_at", sa.DateTime(), nullable=True),
        sa.Column("jd_text", sa.Text(), nullable=True),
        sa.Column("generated_resume_yaml", sa.Text(), nullable=True),
        sa.Column("generated_cover_letter", sa.Text(), nullable=True),
        sa.Column("resume_generated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "url", name="uq_jobs_owner_id_url"),
    )
    op.create_index("ix_jobs_owner_id", "jobs", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_owner_id", table_name="jobs")
    op.drop_table("jobs")
