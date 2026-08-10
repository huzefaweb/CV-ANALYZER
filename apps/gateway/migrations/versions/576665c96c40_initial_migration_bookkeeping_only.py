"""initial migration bookkeeping only

Revision ID: 576665c96c40
Revises: 
Create Date: 2026-08-10 10:10:48.708604

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '576665c96c40'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No feature tables (AC#3): Alembic itself creates only alembic_version."""
    pass


def downgrade() -> None:
    """No feature tables (AC#3): Alembic itself creates only alembic_version."""
    pass
