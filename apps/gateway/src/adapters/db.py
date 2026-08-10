"""Runtime SQLAlchemy engine/session factory.

First runtime DB connection in the gateway process (migrations use their own
connection via alembic's env.py). Kept to a single engine + sessionmaker —
no repository-pattern abstraction until a later story needs more than one.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(database_url: str) -> None:
    global _engine, _SessionLocal
    _engine = create_engine(database_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[OrmSession]:
    if _SessionLocal is None:
        raise RuntimeError("init_engine() must run before get_db()")
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
