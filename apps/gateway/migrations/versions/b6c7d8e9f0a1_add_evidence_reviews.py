"""add_evidence_reviews

Revision ID: b6c7d8e9f0a1
Revises: a535b7aa835c
Create Date: 2026-08-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, Sequence[str], None] = 'a535b7aa835c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "evidence_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("analysis_revision_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("job_requirement_id", sa.String(length=36), nullable=False),
        sa.Column("disputed", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_command_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_evidence_reviews_natural_key",
        "evidence_reviews",
        ["analysis_revision_id", "candidate_id", "job_requirement_id"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_evidence_reviews_natural_key", table_name="evidence_reviews")
    op.drop_table("evidence_reviews")
