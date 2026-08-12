"""add job_requirements, scoring_configurations, candidates, analysis_revisions,
revision_memberships, candidate_jobs; extend start_preparations (Story 3.5,
AR-10-14)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-12 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("start_preparations", sa.Column("attempt", sa.Integer, nullable=False, server_default="1"))
    op.add_column("start_preparations", sa.Column("proposal_json", sa.JSON, nullable=True))
    op.add_column("start_preparations", sa.Column("failure_reason", sa.String(64), nullable=True))

    op.create_table(
        "job_requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_session_id", sa.String(36), nullable=False),
        sa.Column("display_id", sa.String(8), nullable=False),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("classification", sa.String(16), nullable=False),
        sa.Column("canonical_text", sa.Text, nullable=False),
        sa.Column("source_locators", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("analysis_session_id", "display_id", name="uq_job_requirements_session_display_id"),
    )

    op.create_table(
        "scoring_configurations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_session_id", sa.String(36), nullable=False),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("applicable", sa.Boolean, nullable=False),
        sa.Column("effective_weight_bps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("analysis_session_id", "component", name="uq_scoring_configurations_session_component"),
    )

    op.create_table(
        "candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_session_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False, unique=True),
        sa.Column("document_reference", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "analysis_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_session_id", sa.String(36), nullable=False),
        sa.Column("revision_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="frozen"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("analysis_session_id", "revision_number", name="uq_analysis_revisions_session_number"),
    )

    op.create_table(
        "revision_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_revision_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("analysis_revision_id", "candidate_id", name="uq_revision_memberships_revision_candidate"),
    )

    op.create_table(
        "candidate_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_revision_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("analysis_revision_id", "candidate_id", name="uq_candidate_jobs_revision_candidate"),
    )


def downgrade() -> None:
    op.drop_table("candidate_jobs")
    op.drop_table("revision_memberships")
    op.drop_table("analysis_revisions")
    op.drop_table("candidates")
    op.drop_table("scoring_configurations")
    op.drop_table("job_requirements")
    op.drop_column("start_preparations", "failure_reason")
    op.drop_column("start_preparations", "proposal_json")
    op.drop_column("start_preparations", "attempt")
