"""Story 3.1: `POST /new-analysis` (idempotent get-or-create draft) and
`PUT /new-analysis/{id}` (version-checked Job Description save).

Mirrors test_workspace_api.py's live-DATABASE_URL-skip pattern and reuses
its fixture/assertion conventions.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

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


def _seed_session(
    db_session,
    issuer: str,
    subject: str,
    status: str,
    created_at: datetime,
    job_description_text: str = "",
    job_description_version: int = 0,
) -> str:
    session_id = str(uuid.uuid4())
    db_session.add(
        AnalysisSession(
            id=session_id,
            creator_issuer=issuer,
            creator_subject=subject,
            status=status,
            created_at=created_at,
            job_description_text=job_description_text,
            job_description_version=job_description_version,
        )
    )
    db_session.commit()
    return session_id


def test_post_creates_a_draft_when_none_exists(db_session):
    _, token = _admitted_identity_and_token(db_session)
    client.cookies.set("session", token)

    response = client.post("/new-analysis")
    client.cookies.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["job_description_text"] == ""
    assert body["job_description_version"] == 0
    assert body["validation"] == {
        "non_whitespace_count": 0,
        "is_valid": False,
        "minimum_required": 200,
    }


def test_post_is_idempotent_no_duplicate_row(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    client.cookies.set("session", token)

    first = client.post("/new-analysis")
    second = client.post("/new-analysis")
    client.cookies.clear()

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    count = (
        db_session.query(AnalysisSession)
        .filter(
            AnalysisSession.creator_issuer == identity.issuer,
            AnalysisSession.creator_subject == identity.subject,
        )
        .count()
    )
    assert count == 1


def test_post_returns_200_with_reconstructed_state_when_latest_session_is_not_draft(db_session):
    """Story 3.4 (AC#3): a non-draft owned session is reconstructed, not a
    dead-end — and no second session is ever created regardless of status
    code (unchanged from the original 3.1/2.3 guarantee)."""
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(
        db_session, identity.issuer, identity.subject, "preparing_to_start", datetime.now(timezone.utc)
    )
    client.cookies.set("session", token)

    response = client.post("/new-analysis")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == session_id
    assert body["status"] == "preparing_to_start"
    # No StartPreparation row was seeded for this directly-SQL-seeded
    # fixture — the nested projection must be null, not an error.
    assert body["preparation"] is None

    count = (
        db_session.query(AnalysisSession)
        .filter(
            AnalysisSession.creator_issuer == identity.issuer,
            AnalysisSession.creator_subject == identity.subject,
        )
        .count()
    )
    assert count == 1


def test_put_saves_multiline_text_and_increments_version(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject, "draft", datetime.now(timezone.utc))
    client.cookies.set("session", token)

    text = "Line one\nLine two\n" + ("x" * 190)
    response = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": text, "expected_version": 0},
    )
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["job_description_text"] == text
    assert body["job_description_version"] == 1
    assert body["validation"]["is_valid"] is True


def test_put_with_stale_version_returns_current_projection_without_overwrite(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject, "draft", datetime.now(timezone.utc))
    client.cookies.set("session", token)

    first = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": "first save " * 20, "expected_version": 0},
    )
    assert first.status_code == 200
    assert first.json()["job_description_version"] == 1

    stale = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": "stale overwrite attempt " * 20, "expected_version": 0},
    )
    client.cookies.clear()

    assert stale.status_code == 409
    body = stale.json()
    assert body["job_description_text"] == first.json()["job_description_text"]
    assert body["job_description_version"] == 1

    current = db_session.get(AnalysisSession, session_id)
    assert current.job_description_text == first.json()["job_description_text"]
    assert current.job_description_version == 1


def test_put_same_subject_under_a_different_issuer_is_not_owned(db_session):
    """AD-3/AR-8: ownership is the full (issuer, subject) pair, not subject
    alone — mirrors test_workspace_api.py's equivalent coverage for this
    module's own PUT route (authorize_owned_row only proves the subject
    half; the issuer half is this route's own independent re-check)."""
    identity, token = _admitted_identity_and_token(db_session)
    foreign_issuer_session_id = _seed_session(
        db_session, "other-issuer", identity.subject, "draft", datetime.now(timezone.utc)
    )
    client.cookies.set("session", token)

    response = client.put(
        f"/new-analysis/{foreign_issuer_session_id}",
        json={"job_description_text": "x", "expected_version": 0},
    )
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_put_rejects_text_over_the_maximum_length(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject, "draft", datetime.now(timezone.utc))
    client.cookies.set("session", token)

    response = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": "x" * 20_001, "expected_version": 0},
    )
    client.cookies.clear()

    assert response.status_code == 422


def test_concurrent_saves_with_the_same_expected_version_do_not_both_succeed(db_session):
    """Edge/Blind Hunter finding: the version check must be an atomic
    compare-and-swap at the database level, not a read-then-write race. Two
    "concurrent" saves against the same expected_version=0 must not both
    report success — exactly one wins, the other gets a 409 with the
    winner's text, and the row never ends up in a state neither request
    asked for."""
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject, "draft", datetime.now(timezone.utc))
    client.cookies.set("session", token)

    first = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": "writer A " * 20, "expected_version": 0},
    )
    second = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": "writer B " * 20, "expected_version": 0},
    )
    client.cookies.clear()

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409]

    winner = first if first.status_code == 200 else second
    loser = second if first.status_code == 200 else first
    assert loser.json()["job_description_text"] == winner.json()["job_description_text"]

    current = db_session.get(AnalysisSession, session_id)
    assert current.job_description_text == winner.json()["job_description_text"]
    assert current.job_description_version == 1


def test_put_missing_malformed_and_cross_owner_ids_are_neutral(db_session):
    owner_identity, owner_token = _admitted_identity_and_token(db_session)
    _, other_token = _admitted_identity_and_token(db_session)
    owned_id = _seed_session(db_session, owner_identity.issuer, owner_identity.subject, "draft", datetime.now(timezone.utc))

    client.cookies.set("session", other_token)
    cross_owner = client.put(f"/new-analysis/{owned_id}", json={"job_description_text": "x", "expected_version": 0})
    client.cookies.clear()

    client.cookies.set("session", owner_token)
    missing = client.put(f"/new-analysis/{uuid.uuid4()}", json={"job_description_text": "x", "expected_version": 0})
    malformed = client.put("/new-analysis/not-a-real-id", json={"job_description_text": "x", "expected_version": 0})
    oversized = client.put(f"/new-analysis/{'a' * 100}", json={"job_description_text": "x", "expected_version": 0})
    client.cookies.clear()

    for response in (cross_owner, missing, malformed, oversized):
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}


def test_put_against_a_locked_non_draft_session_returns_409(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject, "preparing_to_start", datetime.now(timezone.utc))
    client.cookies.set("session", token)

    response = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": "x" * 200, "expected_version": 0},
    )
    client.cookies.clear()

    assert response.status_code == 409
    assert response.json() == {"detail": "This draft is no longer editable"}


def test_unauthenticated_and_unadmitted_requests_stay_neutral(db_session):
    unauthenticated_post = client.post("/new-analysis")
    assert unauthenticated_post.status_code == 401

    unauthenticated_put = client.put(f"/new-analysis/{uuid.uuid4()}", json={"job_description_text": "x", "expected_version": 0})
    assert unauthenticated_put.status_code == 401

    email = _email()
    local_identity.register(db_session, email, "a-fixture-password")
    not_admitted_session = local_identity.authenticate(db_session, email, "a-fixture-password")
    client.cookies.set("session", not_admitted_session.token)
    not_admitted_post = client.post("/new-analysis")
    not_admitted_put = client.put(f"/new-analysis/{uuid.uuid4()}", json={"job_description_text": "x", "expected_version": 0})
    client.cookies.clear()

    assert not_admitted_post.status_code == 403
    assert not_admitted_put.status_code == 403
