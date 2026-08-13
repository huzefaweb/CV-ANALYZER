"""add lease/fencing columns to start_preparations and candidate_jobs (Story
4.1, AD-6, AR-14)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-13 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("start_preparations", sa.Column("generation", sa.Integer, nullable=False, server_default="0"))
    op.add_column("start_preparations", sa.Column("lease_token", sa.String(32), nullable=True))
    op.add_column("start_preparations", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("start_preparations", sa.Column("state_version", sa.Integer, nullable=False, server_default="0"))
    op.add_column("start_preparations", sa.Column("reclaim_count", sa.Integer, nullable=False, server_default="0"))

    op.add_column("candidate_jobs", sa.Column("generation", sa.Integer, nullable=False, server_default="0"))
    op.add_column("candidate_jobs", sa.Column("lease_token", sa.String(32), nullable=True))
    op.add_column("candidate_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("candidate_jobs", sa.Column("state_version", sa.Integer, nullable=False, server_default="0"))
    op.add_column("candidate_jobs", sa.Column("reclaim_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("candidate_jobs", sa.Column("attempt", sa.Integer, nullable=False, server_default="1"))
    op.add_column("candidate_jobs", sa.Column("failure_reason", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_jobs", "failure_reason")
    op.drop_column("candidate_jobs", "attempt")
    op.drop_column("candidate_jobs", "reclaim_count")
    op.drop_column("candidate_jobs", "lease_expires_at")
    op.drop_column("candidate_jobs", "lease_token")
    op.drop_column("candidate_jobs", "generation")

    op.drop_column("start_preparations", "reclaim_count")
    op.drop_column("start_preparations", "state_version")
    op.drop_column("start_preparations", "lease_expires_at")
    op.drop_column("start_preparations", "lease_token")
    op.drop_column("start_preparations", "generation")
