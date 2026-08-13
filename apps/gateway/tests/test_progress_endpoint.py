"""Story 4.7: the authorized gateway Progress projection
(`GET /workspace/sessions/{id}/progress`) — frozen row order, non-regressing
per-row state, safe-only field exposure, and the suppressed-ranking
all-terminal projection. Mirrors test_candidate_finalizer.py's own-fixture/
autouse-truncate live-DATABASE_URL pattern rather than conftest.py's
identity-only db_session (which does not truncate the Candidate/Revision
chain this story reads).
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
from src.adapters.models import AnalysisRevision, AnalysisSession, Candidate, CandidateJob, Document, RevisionMembership

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
            "TRUNCATE TABLE sessions, users, revision_memberships, candidate_jobs, "
            "candidates, documents, analysis_revisions, analysis_sessions CASCADE"
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


def _seed_revision(db, session_id: str, revision_number: int = 1, published_at: datetime | None = None) -> str:
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


def _seed_candidate_row(
    db,
    *,
    revision_id: str,
    session_id: str,
    document_reference: str,
    created_at: datetime,
    job_status: str,
    outcome: str = "queued",
    attempt: int = 1,
    reclaim_count: int = 0,
    failure_reason: str | None = None,
    document_created_at: datetime | None = None,
    skip_job_row: bool = False,
) -> str:
    """`created_at` is the revision-freeze timestamp shared by every
    Candidate in the same freeze (matching preparation_finalizer.py's real
    single-`now`-per-freeze shape — deliberately NOT distinct per row, so a
    test relying on distinct `created_at` values would be testing a fixture
    shape the real writer never produces). `document_created_at` is each
    Document's own distinct upload timestamp — the real frozen-order key —
    and defaults to `created_at` when the test doesn't care about ordering.
    """
    candidate_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    db.add(
        Document(
            id=document_id,
            analysis_session_id=session_id,
            document_reference=document_reference,
            original_filename=f"{document_reference}.pdf",
            content_version=1,
            storage_path=f"/tmp/{document_id}",
            size_bytes=1024,
            content_type="application/pdf",
            status="ready",
            idempotency_key=str(uuid.uuid4()),
            created_at=document_created_at or created_at,
        )
    )
    db.add(
        Candidate(
            id=candidate_id,
            analysis_session_id=session_id,
            document_id=document_id,
            document_reference=document_reference,
            created_at=created_at,
        )
    )
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            outcome=outcome,
            created_at=created_at,
        )
    )
    if not skip_job_row:
        db.add(
            CandidateJob(
                id=str(uuid.uuid4()),
                analysis_revision_id=revision_id,
                candidate_id=candidate_id,
                status=job_status,
                created_at=created_at,
                attempt=attempt,
                reclaim_count=reclaim_count,
                failure_reason=failure_reason,
            )
        )
    db.commit()
    return candidate_id


def test_frozen_order_is_document_upload_order_not_candidate_freeze_or_completion_order(db):
    """preparation_finalizer.py assigns one shared `now` to every Candidate
    row frozen into a revision — candidates.created_at ties for every row in
    a real freeze. Documents are uploaded one at a time, so documents.created_at
    is the only real per-row ordering signal. This fixture gives both
    Candidates the *same* candidate created_at (the real shape) but distinct
    Document upload timestamps, and D2 finishes before D1 — frozen order
    must still follow Document upload order, not completion order."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id)
    frozen_at = datetime.now(timezone.utc)
    first_id = _seed_candidate_row(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        created_at=frozen_at, document_created_at=frozen_at, job_status="queued",
    )
    second_id = _seed_candidate_row(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2",
        created_at=frozen_at,  # same freeze moment as D1 — the real shape
        document_created_at=frozen_at.replace(microsecond=(frozen_at.microsecond + 1000) % 1_000_000),
        job_status="finalized", outcome="NewResult",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert [r["candidate_id"] for r in body["rows"]] == [first_id, second_id]
    assert body["rows"][0]["state"] == "Queued"
    assert body["rows"][1]["state"] == "Succeeded"


def test_mixed_state_aggregate_and_all_terminal_false(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id)
    now = datetime.now(timezone.utc)
    _seed_candidate_row(db, revision_id=revision_id, session_id=session_id, document_reference="D1", created_at=now, job_status="claimed")
    _seed_candidate_row(db, revision_id=revision_id, session_id=session_id, document_reference="D2", created_at=now, job_status="finalized", outcome="Failed")

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    body = response.json()
    assert body["aggregate"]["total"] == 2
    assert body["aggregate"]["by_state"] == {"Parsing": 1, "Failed": 1}
    assert body["all_terminal"] is False
    assert body["ranking_suppressed"] is False


def test_retrying_and_recovering_states_project_correctly_through_the_endpoint(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id)
    now = datetime.now(timezone.utc)
    _seed_candidate_row(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", created_at=now,
        job_status="queued", attempt=2, failure_reason="lease_exhausted",
    )
    _seed_candidate_row(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2", created_at=now,
        job_status="queued", reclaim_count=1,
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    body = response.json()
    states = {r["document_reference"]: r["state"] for r in body["rows"]}
    assert states["D1"] == "Retrying - Attempt 2 of 2"
    assert states["D2"] == "Recovering"


def test_missing_job_row_is_skipped_without_500ing_or_hiding_other_candidates(db):
    """A membership with no matching candidate_jobs row for its revision is
    an anomaly under Story 3.5/4.1's atomic membership+job creation
    invariant, not an expected shape — but the endpoint must degrade to
    skipping that one row rather than 500ing (previously an uncaught
    ValueError from derive_row_state) or silently dropping every candidate
    via an unqualified INNER JOIN."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id)
    now = datetime.now(timezone.utc)
    _seed_candidate_row(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", created_at=now,
        job_status="queued", skip_job_row=True,
    )
    healthy_id = _seed_candidate_row(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2", created_at=now,
        job_status="claimed",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert [r["candidate_id"] for r in body["rows"]] == [healthy_id]
    assert body["aggregate"]["total"] == 1


def test_all_terminal_suppresses_ranking_and_never_offers_view_results(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id)
    now = datetime.now(timezone.utc)
    _seed_candidate_row(db, revision_id=revision_id, session_id=session_id, document_reference="D1", created_at=now, job_status="finalized", outcome="NewResult")
    _seed_candidate_row(db, revision_id=revision_id, session_id=session_id, document_reference="D2", created_at=now, job_status="finalized", outcome="NeedsReview")

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    body = response.json()
    assert body["all_terminal"] is True
    assert body["ranking_suppressed"] is True
    assert body["view_results_available"] is False


def test_published_revision_makes_view_results_available_and_stops_suppressing_ranking(db):
    """Story 5.1: ranking_suppressed/view_results_available now track the
    real analysis_revisions.published_at column instead of a hard-coded
    constant — this is the positive case (Story 4.7 only ever exercised the
    unpublished/always-suppressed case)."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    now = datetime.now(timezone.utc)
    _seed_candidate_row(db, revision_id=revision_id, session_id=session_id, document_reference="D1", created_at=now, job_status="finalized", outcome="NewResult")

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    body = response.json()
    assert body["all_terminal"] is True
    assert body["ranking_suppressed"] is False
    assert body["view_results_available"] is True


def test_no_revision_yet_is_empty_projection_not_404(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    assert response.status_code == 200
    assert response.json() == {
        "revision_number": None,
        "rows": [],
        "aggregate": {"total": 0, "by_state": {}},
        "all_terminal": False,
        "ranking_suppressed": False,
        "view_results_available": False,
    }


def test_cross_owner_session_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    _, other_token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)

    client.cookies.set("session", other_token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


_ALLOWED_ROW_KEYS = {"candidate_id", "document_reference", "state"}
_ALLOWED_TOP_LEVEL_KEYS = {
    "revision_number", "rows", "aggregate", "all_terminal", "ranking_suppressed", "view_results_available",
}
_ALLOWED_AGGREGATE_KEYS = {"total", "by_state"}


def test_no_internal_field_ever_leaves_the_projection(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id)
    now = datetime.now(timezone.utc)
    _seed_candidate_row(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", created_at=now,
        job_status="failed", attempt=2, failure_reason="Analysis timed out",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/progress")
    client.cookies.clear()

    # Structural key-based check (review finding: a raw substring scan is
    # fragile to both false positives on unrelated text and false negatives
    # on differently-cased/nested leaks) — assert the exact key set at every
    # level of the response, not just the absence of a few banned strings.
    body = response.json()
    assert set(body.keys()) == _ALLOWED_TOP_LEVEL_KEYS
    assert set(body["aggregate"].keys()) == _ALLOWED_AGGREGATE_KEYS
    for row in body["rows"]:
        assert set(row.keys()) == _ALLOWED_ROW_KEYS
