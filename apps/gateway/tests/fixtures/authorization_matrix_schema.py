"""SQLAlchemy Core `Table` for the Story 1.5c harness-only fixture object.

Not production schema — Epic 3+ owns the real descendant tables (Documents,
Analysis Sessions, Candidate Results, ...) this table stands in for. The
`CREATE TABLE` DDL the test suite applies is compiled directly from this
`Table` (via SQLAlchemy's own DDL compiler) rather than hand-duplicated in a
parallel `.sql` file, so there is exactly one source of truth for the
harness schema and no drift risk between two independently maintained
definitions.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table, Text
from sqlalchemy.schema import CreateTable

metadata = MetaData()

authorization_matrix_fixture_object = Table(
    "authorization_matrix_fixture_object",
    metadata,
    Column("object_id", Text, primary_key=True),
    Column("owner_subject", Text, nullable=False),
)

SCHEMA_SQL = str(CreateTable(authorization_matrix_fixture_object, if_not_exists=True))
