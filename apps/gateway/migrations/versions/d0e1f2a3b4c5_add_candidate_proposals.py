"""add candidate_proposals table (Story 4.4, AD-8, AR-24, AR-40)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-13 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_job_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("analysis_revision_id", sa.String(36), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("gate_codes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_candidate_proposals_candidate_job_id",
        "candidate_proposals",
        ["candidate_job_id"],
        unique=True,
    )
    op.create_index("ix_candidate_proposals_candidate_id", "candidate_proposals", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_candidate_proposals_candidate_id", table_name="candidate_proposals")
    op.drop_index("ix_candidate_proposals_candidate_job_id", table_name="candidate_proposals")
    op.drop_table("candidate_proposals")
