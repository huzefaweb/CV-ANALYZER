"""Story 5.4: the authorized revision-listing projection
(`GET /workspace/sessions/{id}/revisions`) — the source list a revision
selector renders. Mirrors test_results_endpoint.py's own-fixture/autouse-
truncate live-DATABASE_URL pattern.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.adapters import local_identity
from src.adapters.api import app
from src.adapters.models import AnalysisRevision, AnalysisSession

VALID_JOB_DESCRIPTION = "x" * 200

_engine = create_engine(os.environ["DATABASE_URL"])
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

client = TestClient(app)


@pytest.fixture()
def db():
    session = _SessionFactory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db):
    db.execute(
        text(
            "TRUNCATE TABLE sessions, users, analysis_revisions, analysis_sessions CASCADE"
        )
    )
    db.commit()
    yield


def _email() -> str:
    return f"user-{uuid.uuid4()}@example.com"


def _admitted_identity_and_token(db_session):
    email = _email()
    identity = local_identity.register(db_session, email, "a-fixture-password")
    local_identity.admit_user(db_session, identity.subject)
    session = local_identity.authenticate(db_session, email, "a-fixture-password")
    return identity, session.token


def _seed_session(db, issuer: str, subject: str) -> str:
    session_id = str(uuid.uuid4())
    db.add(
        AnalysisSession(
            id=session_id,
            creator_issuer=issuer,
            creator_subject=subject,
            status="frozen_inputs",
            created_at=datetime.now(timezone.utc),
            job_description_text=VALID_JOB_DESCRIPTION,
            job_description_version=1,
        )
    )
    db.commit()
    return session_id


def _seed_revision(db, session_id: str, revision_number: int, published_at: datetime | None = None) -> str:
    revision_id = str(uuid.uuid4())
    db.add(
        AnalysisRevision(
            id=revision_id,
            analysis_session_id=session_id,
            revision_number=revision_number,
            status="frozen",
            created_at=datetime.now(timezone.utc),
            published_at=published_at,
        )
    )
    db.commit()
    return revision_id


def test_zero_revisions_returns_empty_list(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/revisions")
    client.cookies.clear()

    assert response.status_code == 200
    assert response.json() == {"revisions": []}


def test_single_published_revision_is_current(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    published_at = datetime.now(timezone.utc)
    _seed_revision(db, session_id, 1, published_at=published_at)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/revisions")
    client.cookies.clear()

    body = response.json()
    assert body["revisions"] == [
        {
            "revision_number": 1,
            "published": True,
            "published_at": published_at.isoformat(),
            "is_current": True,
        }
    ]


def test_published_source_plus_processing_retry_revision(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    published_at = datetime.now(timezone.utc)
    _seed_revision(db, session_id, 1, published_at=published_at)
    _seed_revision(db, session_id, 2, published_at=None)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/revisions")
    client.cookies.clear()

    body = response.json()
    by_number = {r["revision_number"]: r for r in body["revisions"]}
    assert by_number[1]["published"] is True
    assert by_number[1]["is_current"] is True
    assert by_number[2]["published"] is False
    assert by_number[2]["is_current"] is False
    # Descending order — most-relevant (latest) revision first.
    assert [r["revision_number"] for r in body["revisions"]] == [2, 1]


def test_two_published_revisions_only_the_latest_is_current(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_revision(db, session_id, 1, published_at=datetime.now(timezone.utc))
    _seed_revision(db, session_id, 2, published_at=datetime.now(timezone.utc))

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/revisions")
    client.cookies.clear()

    body = response.json()
    by_number = {r["revision_number"]: r for r in body["revisions"]}
    assert by_number[1]["is_current"] is False
    assert by_number[2]["is_current"] is True


def test_cross_owner_session_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    _, other_token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)

    client.cookies.set("session", other_token)
    response = client.get(f"/workspace/sessions/{session_id}/revisions")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
