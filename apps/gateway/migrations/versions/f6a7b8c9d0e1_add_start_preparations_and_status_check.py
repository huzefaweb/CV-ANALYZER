"""add start_preparations table and analysis_sessions.status check (Story 3.4, AR-9, AR-10)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Defensive normalization (review finding): any row seeded with a
    # legacy/ad-hoc status value outside the three now-enumerated states
    # (e.g. a pre-3.4 test fixture's "preparing") would otherwise make the
    # CHECK constraint below fail the whole migration outright. This
    # codebase has only ever written "draft" in production code paths, so
    # this is a no-op on a clean database and a safety net, not a real
    # data-migration concern.
    op.execute(
        "UPDATE analysis_sessions SET status = 'draft' "
        "WHERE status NOT IN ('draft', 'preparing_to_start', 'frozen_inputs')"
    )
    op.create_check_constraint(
        "ck_analysis_sessions_status",
        "analysis_sessions",
        "status IN ('draft', 'preparing_to_start', 'frozen_inputs')",
    )
    op.create_table(
        "start_preparations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_session_id", sa.String(36), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("job_description_version", sa.Integer, nullable=False),
        sa.Column("document_versions", sa.JSON, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("start_preparations")
    op.drop_constraint("ck_analysis_sessions_status", "analysis_sessions", type_="check")
