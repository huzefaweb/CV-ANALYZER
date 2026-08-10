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


@pytest.fixture()
def db_session(engine):
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    session.execute(text("TRUNCATE TABLE sessions, users RESTART IDENTITY CASCADE"))
    session.commit()
    yield session
    session.close()
