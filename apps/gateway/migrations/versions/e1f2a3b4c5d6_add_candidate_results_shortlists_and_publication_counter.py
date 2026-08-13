"""add candidate_results, shortlists, and analysis_revisions.requested_version (Story 4.6, AR-12, AR-19, AR-27, AR-33)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-13 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_revision_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("candidate_job_id", sa.String(36), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("overall_score_bps_numerator", sa.Numeric(), nullable=True),
        sa.Column("overall_score_bps_denominator", sa.Numeric(), nullable=True),
        sa.Column("mandatory_skills_score_numerator", sa.Numeric(), nullable=True),
        sa.Column("mandatory_skills_score_denominator", sa.Numeric(), nullable=True),
        sa.Column("relevant_experience_score_numerator", sa.Numeric(), nullable=True),
        sa.Column("relevant_experience_score_denominator", sa.Numeric(), nullable=True),
        sa.Column("coverage_bps_numerator", sa.Numeric(), nullable=True),
        sa.Column("coverage_bps_denominator", sa.Numeric(), nullable=True),
        sa.Column("precise_score_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("headline_whole_percent", sa.Integer(), nullable=True),
        sa.Column("component_contribution_display", sa.JSON(), nullable=True),
        sa.Column("gate_codes", sa.JSON(), nullable=True),
        sa.Column("failure_category", sa.String(64), nullable=True),
        sa.Column("failure_correlation_reference", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_candidate_results_revision_candidate",
        "candidate_results",
        ["analysis_revision_id", "candidate_id"],
        unique=True,
    )

    op.create_table(
        "shortlists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="NotShortlisted"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shortlists_candidate_id", "shortlists", ["candidate_id"], unique=True)

    op.add_column(
        "analysis_revisions",
        sa.Column("requested_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("analysis_revisions", "requested_version")
    op.drop_index("ix_shortlists_candidate_id", table_name="shortlists")
    op.drop_table("shortlists")
    op.drop_index("ix_candidate_results_revision_candidate", table_name="candidate_results")
    op.drop_table("candidate_results")
