"""add_document_retry_allowance

Revision ID: a535b7aa835c
Revises: f2a3b4c5d6e7
Create Date: 2026-08-14 09:22:38.525190

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a535b7aa835c'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("retried_into_revision_id", sa.String(length=36), nullable=True))
    op.add_column("documents", sa.Column("retry_idempotency_key", sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "retry_idempotency_key")
    op.drop_column("documents", "retried_into_revision_id")
