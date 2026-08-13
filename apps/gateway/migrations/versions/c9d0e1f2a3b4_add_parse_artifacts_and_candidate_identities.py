"""add parse_artifacts and candidate_identities tables (Story 4.2, AD-8, AD-9)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parse_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("document_content_version", sa.Integer(), nullable=False),
        sa.Column("parser_pipeline_version", sa.String(16), nullable=False),
        sa.Column("source_units_json", sa.JSON(), nullable=False),
        sa.Column("blocks_json", sa.JSON(), nullable=False),
        sa.Column("gate_codes", sa.JSON(), nullable=False),
        sa.Column("coherent_block_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_parse_artifacts_document_version_pipeline",
        "parse_artifacts",
        ["document_id", "document_content_version", "parser_pipeline_version"],
        unique=True,
    )
    op.create_index("ix_parse_artifacts_candidate_id", "parse_artifacts", ["candidate_id"])

    op.create_table(
        "candidate_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("name_source", sa.String(16), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_candidate_identities_candidate_id",
        "candidate_identities",
        ["candidate_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_identities_candidate_id", table_name="candidate_identities")
    op.drop_table("candidate_identities")

    op.drop_index("ix_parse_artifacts_candidate_id", table_name="parse_artifacts")
    op.drop_index("ix_parse_artifacts_document_version_pipeline", table_name="parse_artifacts")
    op.drop_table("parse_artifacts")
