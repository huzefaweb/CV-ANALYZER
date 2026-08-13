"""Story 5.2: the authorized gateway Results projection
(`GET /workspace/sessions/{id}/results`) — current-published-revision-only,
Ranked/Needs Review/Failed family separation, and safe-only field exposure.
Mirrors test_progress_endpoint.py's own-fixture/autouse-truncate live-
DATABASE_URL pattern.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.adapters import local_identity
from src.adapters.api import app
from src.adapters.models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateIdentity,
    CandidateJob,
    CandidateProposal,
    CandidateResult,
    Document,
    JobRequirement,
    RevisionMembership,
    Shortlist,
)

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
            "TRUNCATE TABLE sessions, users, shortlists, candidate_proposals, candidate_results, "
            "candidate_identities, revision_memberships, candidate_jobs, job_requirements, "
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
            ranked_count=0,
            needs_review_count=0,
            failed_count=0,
        )
    )
    db.commit()
    return revision_id


def _seed_requirement(db, session_id: str, display_id: str, component: str, canonical_text: str) -> str:
    requirement_id = str(uuid.uuid4())
    db.add(
        JobRequirement(
            id=requirement_id,
            analysis_session_id=session_id,
            display_id=display_id,
            component=component,
            classification="mandatory",
            canonical_text=canonical_text,
            source_locators=[],
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return requirement_id


def _seed_candidate(
    db,
    *,
    revision_id: str,
    session_id: str,
    document_reference: str,
    outcome: str,
    display_name: str | None = None,
    rank_position: int | None = None,
    tie_group: int | None = None,
    presentation_ordinal: int | None = None,
    headline_whole_percent: int | None = None,
    gate_codes: list | None = None,
    failure_category: str | None = None,
    proposal_items: list | None = None,
    shortlist_state: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
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
            created_at=now,
        )
    )
    db.add(
        Candidate(
            id=candidate_id,
            analysis_session_id=session_id,
            document_id=document_id,
            document_reference=document_reference,
            created_at=now,
        )
    )
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            outcome=outcome,
            created_at=now,
            rank_position=rank_position,
            tie_group=tie_group,
            presentation_ordinal=presentation_ordinal,
        )
    )
    if display_name is not None:
        db.add(
            CandidateIdentity(
                id=str(uuid.uuid4()),
                candidate_id=candidate_id,
                display_name=display_name,
                name_source="parsed",
                created_at=now,
            )
        )
    candidate_job_id = str(uuid.uuid4())
    db.add(
        CandidateJob(
            id=candidate_job_id,
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            status="finalized",
            created_at=now,
        )
    )
    result_fields = dict(
        headline_whole_percent=headline_whole_percent,
        gate_codes=gate_codes,
        failure_category=failure_category,
        failure_correlation_reference=candidate_job_id if outcome == "Failed" else None,
    )
    if headline_whole_percent is not None:
        result_fields.update(
            overall_score_bps_numerator=Decimal(headline_whole_percent * 100),
            overall_score_bps_denominator=Decimal(1),
            precise_score_percent=Decimal(headline_whole_percent),
        )
    db.add(
        CandidateResult(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            candidate_job_id=candidate_job_id,
            outcome=outcome,
            created_at=now,
            **result_fields,
        )
    )
    if proposal_items is not None:
        db.add(
            CandidateProposal(
                id=str(uuid.uuid4()),
                candidate_job_id=candidate_job_id,
                candidate_id=candidate_id,
                analysis_revision_id=revision_id,
                items_json=proposal_items,
                gate_codes=[],
                created_at=now,
            )
        )
    if shortlist_state is not None:
        db.add(
            Shortlist(
                id=str(uuid.uuid4()),
                candidate_id=candidate_id,
                state=shortlist_state,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    return candidate_id


def test_no_current_published_revision_returns_neutral_not_yet_published(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_revision(db, session_id)  # unpublished

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["published"] is False
    assert body["revision_number"] is None
    assert body["published_at"] is None
    assert body["counts"] == {"ranked": 0, "needs_review": 0, "failed": 0}
    assert body["ranked"] == []
    assert body["needs_review"] == []
    assert body["failed"] == []
    assert "notice" in body


def test_mixed_outcome_revision_separates_families_and_counts(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    req_id = _seed_requirement(db, session_id, "R1", "mandatory_skills", "Kubernetes operations")
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))

    ranked_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        display_name="Jordan Lee",
        rank_position=1,
        tie_group=1,
        presentation_ordinal=1,
        headline_whole_percent=86,
        proposal_items=[{"job_requirement_id": req_id, "state": "Matched", "locator": "u1", "excerpt": "K8s"}],
        shortlist_state="Shortlisted",
    )
    needs_review_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D2",
        outcome="NeedsReview",
        gate_codes=["TEXT_BELOW_500"],
    )
    failed_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D3",
        outcome="Failed",
        failure_category="Analysis timed out",
    )

    db.execute(
        AnalysisRevision.__table__.update()
        .where(AnalysisRevision.__table__.c.id == revision_id)
        .values(ranked_count=1, needs_review_count=1, failed_count=1)
    )
    db.commit()

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["published"] is True
    assert body["counts"] == {"ranked": 1, "needs_review": 1, "failed": 1}

    assert len(body["ranked"]) == 1
    ranked_row = body["ranked"][0]
    assert ranked_row["candidate_id"] == ranked_id
    assert ranked_row["display_name"] == "Jordan Lee"
    assert ranked_row["headline_whole_percent"] == 86
    assert ranked_row["shortlist_state"] == "Shortlisted"
    assert ranked_row["strengths"] == [{"requirement_text": "Kubernetes operations", "state": "Matched"}]
    assert ranked_row["gaps"] == []

    assert len(body["needs_review"]) == 1
    assert body["needs_review"][0]["candidate_id"] == needs_review_id
    assert body["needs_review"][0]["gate_codes"] == ["TEXT_BELOW_500"]
    assert body["needs_review"][0]["shortlist_state"] == "NotShortlisted"

    assert len(body["failed"]) == 1
    assert body["failed"][0]["candidate_id"] == failed_id
    assert body["failed"][0]["failure_category"] == "Analysis timed out"


def test_tied_ranked_rows_share_rank_position(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="NewResult",
        rank_position=1, tie_group=1, presentation_ordinal=1, headline_whole_percent=82,
    )
    _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2", outcome="NewResult",
        rank_position=1, tie_group=1, presentation_ordinal=2, headline_whole_percent=82,
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()

    body = response.json()
    ranks = [row["rank_position"] for row in body["ranked"]]
    assert ranks == [1, 1]
    assert [row["presentation_ordinal"] for row in body["ranked"]] == [1, 2]


def test_candidate_with_no_identity_row_falls_back_to_document_reference(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D7K2", outcome="NewResult",
        rank_position=1, tie_group=1, presentation_ordinal=1, headline_whole_percent=70,
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()

    body = response.json()
    assert body["ranked"][0]["display_name"] == "D7K2"


def test_ranked_membership_with_no_matching_result_row_is_skipped_without_500ing(db):
    """A `NewResult`/`ReusedResult` membership with no matching
    `candidate_results` row is unreachable given `candidate_finalizer.py`'s
    atomic write (Story 4.6) — but the endpoint must degrade to skipping
    that one anomalous row rather than crashing the whole request, and it
    must not hide every other Candidate in the session."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))

    now = datetime.now(timezone.utc)
    anomalous_candidate_id = str(uuid.uuid4())
    anomalous_document_id = str(uuid.uuid4())
    db.add(
        Document(
            id=anomalous_document_id, analysis_session_id=session_id, document_reference="ANOM",
            original_filename="anom.pdf", content_version=1, storage_path="/tmp/anom", size_bytes=1024,
            content_type="application/pdf", status="ready", idempotency_key=str(uuid.uuid4()), created_at=now,
        )
    )
    db.add(
        Candidate(
            id=anomalous_candidate_id, analysis_session_id=session_id, document_id=anomalous_document_id,
            document_reference="ANOM", created_at=now,
        )
    )
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()), analysis_revision_id=revision_id, candidate_id=anomalous_candidate_id,
            outcome="NewResult", created_at=now, rank_position=1, tie_group=1, presentation_ordinal=1,
        )
    )
    db.commit()
    # No CandidateResult row for this membership — the anomaly under test.

    healthy_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="NewResult",
        rank_position=1, tie_group=1, presentation_ordinal=2, headline_whole_percent=70,
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert [row["candidate_id"] for row in body["ranked"]] == [healthy_id]


def test_cross_owner_session_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    _, other_token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)

    client.cookies.set("session", other_token)
    response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
