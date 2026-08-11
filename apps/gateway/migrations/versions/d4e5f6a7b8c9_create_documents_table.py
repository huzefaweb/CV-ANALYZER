"""create documents table (Story 3.2, AR-8, AR-36)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_session_id", sa.String(36), nullable=False),
        sa.Column("document_reference", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("storage_path", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_documents_session_reference",
        "documents",
        ["analysis_session_id", "document_reference"],
        unique=True,
    )
    op.create_index(
        "ix_documents_session_idempotency_key",
        "documents",
        ["analysis_session_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_session_idempotency_key", table_name="documents")
    op.drop_index("ix_documents_session_reference", table_name="documents")
    op.drop_table("documents")
