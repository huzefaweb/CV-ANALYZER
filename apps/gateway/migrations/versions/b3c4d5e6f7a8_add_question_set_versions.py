"""add question_set_versions table (Story 7.2, AR-17, AR-34)

Revision ID: b3c4d5e6f7a8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_set_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("question_set_job_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("analysis_revision_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # One published version per finalized job (mirrors
    # question_set_proposals' exactly-once-per-job shape).
    op.create_index(
        "ix_question_set_versions_question_set_job_id",
        "question_set_versions",
        ["question_set_job_id"],
        unique=True,
    )
    # Supports "the selected complete set" = MAX(version) for a
    # (candidate_id, analysis_revision_id) pair. Never observes a second
    # version in V1 (see model docstring) but kept unique/composite so a
    # future relaxation of the retry guard cannot silently double-publish.
    op.create_index(
        "ix_question_set_versions_candidate_revision_version",
        "question_set_versions",
        ["candidate_id", "analysis_revision_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_question_set_versions_candidate_revision_version", table_name="question_set_versions"
    )
    op.drop_index(
        "ix_question_set_versions_question_set_job_id", table_name="question_set_versions"
    )
    op.drop_table("question_set_versions")
