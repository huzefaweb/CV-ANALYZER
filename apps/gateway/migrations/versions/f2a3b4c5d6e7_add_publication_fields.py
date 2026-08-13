"""add publication fields to analysis_revisions and revision_memberships (Story 5.1, AR-19, AR-20, AR-30)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-13 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analysis_revisions", sa.Column("published_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("analysis_revisions", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analysis_revisions", sa.Column("ranked_count", sa.Integer(), nullable=True))
    op.add_column("analysis_revisions", sa.Column("needs_review_count", sa.Integer(), nullable=True))
    op.add_column("analysis_revisions", sa.Column("failed_count", sa.Integer(), nullable=True))

    op.add_column("revision_memberships", sa.Column("rank_position", sa.Integer(), nullable=True))
    op.add_column("revision_memberships", sa.Column("tie_group", sa.Integer(), nullable=True))
    op.add_column("revision_memberships", sa.Column("presentation_ordinal", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("revision_memberships", "presentation_ordinal")
    op.drop_column("revision_memberships", "tie_group")
    op.drop_column("revision_memberships", "rank_position")

    op.drop_column("analysis_revisions", "failed_count")
    op.drop_column("analysis_revisions", "needs_review_count")
    op.drop_column("analysis_revisions", "ranked_count")
    op.drop_column("analysis_revisions", "published_at")
    op.drop_column("analysis_revisions", "published_version")
