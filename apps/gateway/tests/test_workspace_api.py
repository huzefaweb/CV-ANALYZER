"""Story 2.3: workspace entry shows only the latest creator-owned Analysis
Session (AC#1/AC#2), and a direct-object session URL is neutral for missing,
malformed, and cross-owner ids alike (AC#3, AD-3).

No live Analysis Session creation flow exists yet (Epic 3+) — "owned work" is
proven via direct-fixture row insertion, matching the Story 1.5c precedent.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient

from src.adapters import local_identity
from src.adapters.api import app
from src.adapters.models import AnalysisSession

client = TestClient(app)


def _email() -> str:
    return f"user-{uuid.uuid4()}@example.com"


def _admitted_identity_and_token(db_session):
    email = _email()
    identity = local_identity.register(db_session, email, "a-fixture-password")
    local_identity.admit_user(db_session, identity.subject)
    session = local_identity.authenticate(db_session, email, "a-fixture-password")
    return identity, session.token


def _seed_session(db_session, issuer: str, subject: str, status: str, created_at: datetime) -> str:
    session_id = str(uuid.uuid4())
    db_session.add(
        AnalysisSession(
            id=session_id,
            creator_issuer=issuer,
            creator_subject=subject,
            status=status,
            created_at=created_at,
        )
    )
    db_session.commit()
    return session_id


def test_no_owned_session_returns_null(db_session):
    _, token = _admitted_identity_and_token(db_session)
    client.cookies.set("session", token)

    response = client.get("/workspace")

    assert response.status_code == 200
    assert response.json() == {"session": None}
    client.cookies.clear()


def test_owned_session_is_projected(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(
        db_session, identity.issuer, identity.subject, "draft", datetime.now(timezone.utc)
    )
    client.cookies.set("session", token)

    response = client.get("/workspace")

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == session_id
    assert body["session"]["status"] == "draft"
    assert "created_at" in body["session"]
    client.cookies.clear()


def test_only_the_latest_owned_session_is_ever_returned(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    now = datetime.now(timezone.utc)
    _seed_session(db_session, identity.issuer, identity.subject, "draft", now - timedelta(hours=1))
    latest_id = _seed_session(db_session, identity.issuer, identity.subject, "draft", now)
    client.cookies.set("session", token)

    response = client.get("/workspace")

    assert response.status_code == 200
    assert response.json() == {
        "session": {
            "id": latest_id,
            "status": "draft",
            "created_at": response.json()["session"]["created_at"],
        }
    }
    client.cookies.clear()


def test_cross_owner_session_never_appears_as_latest(db_session):
    _, owner_token = _admitted_identity_and_token(db_session)
    other_identity, _ = _admitted_identity_and_token(db_session)
    _seed_session(
        db_session, other_identity.issuer, other_identity.subject, "draft", datetime.now(timezone.utc)
    )
    client.cookies.set("session", owner_token)

    response = client.get("/workspace")

    assert response.status_code == 200
    assert response.json() == {"session": None}
    client.cookies.clear()


def test_owner_can_open_owned_session_by_id(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(
        db_session, identity.issuer, identity.subject, "draft", datetime.now(timezone.utc)
    )
    client.cookies.set("session", token)

    response = client.get(f"/workspace/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["id"] == session_id
    client.cookies.clear()


def test_missing_malformed_and_cross_owner_session_ids_are_identically_neutral(db_session):
    owner_identity, owner_token = _admitted_identity_and_token(db_session)
    _, other_token = _admitted_identity_and_token(db_session)
    owned_id = _seed_session(
        db_session, owner_identity.issuer, owner_identity.subject, "draft", datetime.now(timezone.utc)
    )

    client.cookies.set("session", other_token)
    cross_owner = client.get(f"/workspace/sessions/{owned_id}")
    client.cookies.clear()

    client.cookies.set("session", owner_token)
    missing = client.get(f"/workspace/sessions/{uuid.uuid4()}")
    malformed = client.get("/workspace/sessions/not-a-real-id")
    client.cookies.clear()

    for response in (cross_owner, missing, malformed):
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}

    assert cross_owner.headers["content-type"] == missing.headers["content-type"] == malformed.headers["content-type"]


def test_unauthenticated_and_unadmitted_requests_stay_neutral(db_session):
    unauthenticated = client.get("/workspace")
    assert unauthenticated.status_code == 401

    email = _email()
    local_identity.register(db_session, email, "a-fixture-password")
    not_admitted_session = local_identity.authenticate(db_session, email, "a-fixture-password")
    client.cookies.set("session", not_admitted_session.token)
    not_admitted = client.get("/workspace")
    client.cookies.clear()

    assert not_admitted.status_code == 403


def test_direct_session_route_also_stays_neutral_when_unauthenticated_or_unadmitted(db_session):
    """AC#3's URL is a separate route from AC#1/#2's `/workspace` — its own
    admission boundary must be independently proven, not assumed from the
    list endpoint's coverage."""
    unauthenticated = client.get(f"/workspace/sessions/{uuid.uuid4()}")
    assert unauthenticated.status_code == 401

    email = _email()
    local_identity.register(db_session, email, "a-fixture-password")
    not_admitted_session = local_identity.authenticate(db_session, email, "a-fixture-password")
    client.cookies.set("session", not_admitted_session.token)
    not_admitted = client.get(f"/workspace/sessions/{uuid.uuid4()}")
    client.cookies.clear()

    assert not_admitted.status_code == 403


def test_same_subject_under_a_different_issuer_is_not_owned(db_session):
    """AD-3/AR-8: ownership is the full `(issuer, subject)` pair, not subject
    alone. A row whose `creator_subject` happens to match the caller's but
    whose `creator_issuer` does not must be excluded from both the latest-
    session projection and the direct-object route, exactly like any other
    cross-owner row."""
    identity, token = _admitted_identity_and_token(db_session)
    foreign_issuer_session_id = _seed_session(
        db_session, "other-issuer", identity.subject, "draft", datetime.now(timezone.utc)
    )
    client.cookies.set("session", token)

    latest = client.get("/workspace")
    by_id = client.get(f"/workspace/sessions/{foreign_issuer_session_id}")
    client.cookies.clear()

    assert latest.json() == {"session": None}
    assert by_id.status_code == 404
    assert by_id.json() == {"detail": "Not found"}


def test_oversized_session_id_is_neutral_not_a_server_error(db_session):
    """The id column is String(36); an oversized id must still return the
    same neutral 404 rather than reaching Postgres and surfacing a
    truncation error (NFR-12: no infrastructure detail on failure)."""
    _, token = _admitted_identity_and_token(db_session)
    client.cookies.set("session", token)

    response = client.get(f"/workspace/sessions/{'a' * 100}")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
