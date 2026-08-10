"""Shared DB fixtures for identity tests (Story 1.2).

Story 1.1 established PostgreSQL-only as an invariant (never SQLite/in-memory
as proof of compatibility) — these fixtures talk to the real DATABASE_URL,
skipping gracefully when it isn't set rather than faking it.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def engine():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not set; identity tests require a live PostgreSQL instance")
    return create_engine(database_url)


@pytest.fixture(scope="session")
def _authorization_matrix_schema(engine):
    """Applies Story 1.5c's harness-only fixture table (never Alembic).

    Only requested (indirectly, via db_session) by tests that already need a
    live DATABASE_URL — never autouse, so DB-less test modules are unaffected.
    """
    from tests.fixtures.authorization_matrix_schema import SCHEMA_SQL

    with engine.begin() as conn:
        conn.execute(text(SCHEMA_SQL))


@pytest.fixture()
def db_session(engine, _authorization_matrix_schema):
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    session.execute(
        text(
            "TRUNCATE TABLE sessions, users, authorization_matrix_fixture_object "
            "RESTART IDENTITY CASCADE"
        )
    )
    session.commit()
    yield session
    session.close()
