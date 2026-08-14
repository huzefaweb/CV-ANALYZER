"""add question_set_jobs and question_set_proposals tables (Story 7.1, AR-14-17, AR-34)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_set_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("analysis_revision_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reclaim_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("failure_reason", sa.String(64), nullable=True),
    )
    # Code review fix (Acceptance Auditor, High): unique on
    # (candidate_id, analysis_revision_id), not candidate_id alone — AC#1
    # requires "one versioned operation/job exists for that [Candidate]
    # Result", and a Candidate-only key made a job for one revision's
    # successful Result permanently block every other revision's Result
    # from ever getting one (proven by the diff's own now-removed
    # active_generation_exists test). Revision-scoped uniqueness mirrors
    # candidate_jobs's own per-membership shape.
    op.create_index(
        "ix_question_set_jobs_candidate_revision",
        "question_set_jobs",
        ["candidate_id", "analysis_revision_id"],
        unique=True,
    )

    op.create_table(
        "question_set_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("question_set_job_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("analysis_revision_id", sa.String(36), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_question_set_proposals_question_set_job_id",
        "question_set_proposals",
        ["question_set_job_id"],
        unique=True,
    )
    op.create_index(
        "ix_question_set_proposals_candidate_id", "question_set_proposals", ["candidate_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_question_set_proposals_candidate_id", table_name="question_set_proposals")
    op.drop_index(
        "ix_question_set_proposals_question_set_job_id", table_name="question_set_proposals"
    )
    op.drop_table("question_set_proposals")

    op.drop_index("ix_question_set_jobs_candidate_revision", table_name="question_set_jobs")
    op.drop_table("question_set_jobs")
