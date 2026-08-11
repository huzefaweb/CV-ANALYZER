"""create analysis_sessions table (Story 2.3, AD-3)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-11 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("creator_issuer", sa.String(length=255), nullable=False),
        sa.Column("creator_subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_analysis_sessions_creator_created_at",
        "analysis_sessions",
        ["creator_issuer", "creator_subject", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_sessions_creator_created_at", table_name="analysis_sessions")
    op.drop_table("analysis_sessions")
