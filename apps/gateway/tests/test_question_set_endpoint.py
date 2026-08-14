"""Story 7.1: the authorized, idempotent Interview Question Set generation
command (`POST /workspace/candidates/{candidate_id}/question-set`). Mirrors
test_shortlist_endpoint.py's own-fixture/autouse-truncate live-DATABASE_URL
pattern.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from src.adapters import local_identity
from src.adapters.api import app
from src.adapters.models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateJob,
    CandidateResult,
    Document,
    QuestionSetJob,
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
            "TRUNCATE TABLE sessions, users, question_set_proposals, question_set_jobs, shortlists, "
            "evidence_reviews, candidate_proposals, candidate_results, candidate_identities, parse_artifacts, "
            "revision_memberships, candidate_jobs, job_requirements, candidates, documents, "
            "analysis_revisions, analysis_sessions CASCADE"
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


def _seed_candidate(db, *, revision_id: str, session_id: str, document_reference: str, outcome: str) -> str:
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
    db.commit()

    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            outcome=outcome,
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
    result_fields = {}
    if outcome not in ("NeedsReview", "Failed"):
        result_fields.update(
            headline_whole_percent=70,
            overall_score_bps_numerator=Decimal(7000),
            overall_score_bps_denominator=Decimal(1),
            precise_score_percent=Decimal("70.00"),
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
    db.add(
        Shortlist(
            id=str(uuid.uuid4()),
            candidate_id=candidate_id,
            state="NotShortlisted",
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return candidate_id


def _seed_membership_on_existing_candidate(
    db, *, revision_id: str, candidate_id: str, outcome: str
) -> None:
    """Adds a second RevisionMembership/CandidateResult for an already-seeded
    Candidate against a new revision — mirrors a retry producing a second
    published revision for the same Candidate, without re-seeding the
    Candidate/Document rows themselves."""
    now = datetime.now(timezone.utc)
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            outcome=outcome,
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
    db.add(
        CandidateResult(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            candidate_job_id=candidate_job_id,
            outcome=outcome,
            created_at=now,
            headline_whole_percent=70,
            overall_score_bps_numerator=Decimal(7000),
            overall_score_bps_denominator=Decimal(1),
            precise_score_percent=Decimal("70.00"),
        )
    )
    db.commit()


def _seed_ranked_candidate(db, *, document_reference="D1", revision_number=1):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(
        db, session_id, revision_number=revision_number, published_at=datetime.now(timezone.utc)
    )
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference=document_reference, outcome="NewResult"
    )
    return identity, token, session_id, revision_id, candidate_id


def _post(client, token, candidate_id, idempotency_key, revision_number=None):
    client.cookies.set("session", token)
    url = f"/workspace/candidates/{candidate_id}/question-set"
    if revision_number is not None:
        url += f"?revision_number={revision_number}"
    response = client.post(url, json={"idempotency_key": idempotency_key})
    client.cookies.clear()
    return response


def test_first_request_creates_a_job(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)

    response = _post(client, token, candidate_id, "key-1")

    assert response.status_code == 202
    assert response.json() == {"question_set_state": "Generating"}
    row = db.execute(select(QuestionSetJob).where(QuestionSetJob.candidate_id == candidate_id)).scalars().one()
    assert row.status == "queued"


def test_true_replay_returns_same_state_without_a_second_row(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)
    _post(client, token, candidate_id, "key-1")

    replay = _post(client, token, candidate_id, "key-1")

    assert replay.status_code == 202
    assert replay.json() == {"question_set_state": "Generating"}
    count = db.execute(select(QuestionSetJob).where(QuestionSetJob.candidate_id == candidate_id)).scalars().all()
    assert len(count) == 1


def test_duplicate_refresh_with_a_different_key_same_target_is_not_a_second_job(db):
    _, token, _, revision_id, candidate_id = _seed_ranked_candidate(db)
    _post(client, token, candidate_id, "key-1", revision_number=1)

    refreshed = _post(client, token, candidate_id, "key-2", revision_number=1)

    assert refreshed.status_code == 202
    assert refreshed.json() == {"question_set_state": "Generating"}
    count = db.execute(select(QuestionSetJob).where(QuestionSetJob.candidate_id == candidate_id)).scalars().all()
    assert len(count) == 1


def test_second_published_revision_gets_its_own_independent_job(db):
    """Code review fix (Acceptance Auditor, High): question_set_jobs is
    unique on (candidate_id, analysis_revision_id), not candidate_id alone —
    AC#1's "one versioned operation/job exists for that [Candidate] Result"
    must hold for EVERY successful Result, not just the first revision that
    happens to ask. A retry that produces a second published revision with
    its own NewResult must be independently generatable, never blocked by
    an earlier revision's job."""
    identity, token, session_id, revision_1_id, candidate_id = _seed_ranked_candidate(db, revision_number=1)
    first = _post(client, token, candidate_id, "key-1", revision_number=1)
    assert first.status_code == 202

    revision_2_id = _seed_revision(db, session_id, revision_number=2, published_at=datetime.now(timezone.utc))
    _seed_membership_on_existing_candidate(db, revision_id=revision_2_id, candidate_id=candidate_id, outcome="NewResult")

    second = _post(client, token, candidate_id, "key-2", revision_number=2)

    assert second.status_code == 202
    assert second.json() == {"question_set_state": "Generating"}
    rows = db.execute(select(QuestionSetJob).where(QuestionSetJob.candidate_id == candidate_id)).scalars().all()
    assert len(rows) == 2
    assert {r.analysis_revision_id for r in rows} == {revision_1_id, revision_2_id}


def test_concurrent_first_requests_race_exactly_one_job_row_both_report_generating(db):
    """Two concurrent first-time requests for the same (candidate, revision)
    race the unique-(candidate_id, analysis_revision_id) insert; the
    loser's IntegrityError recovery re-reads and finds the winner's row
    already exists for that exact key, so both resolve to 202/"Generating"
    — a duplicate command racing its own first delivery, not a conflict
    (AC#1: "requested once, duplicated, or refreshed... one versioned
    operation/job exists")."""
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)

    def attempt(key):
        return _post(TestClient(app), token, candidate_id, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(attempt, ["key-a", "key-b"]))

    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json() == {"question_set_state": "Generating"}
    assert r2.json() == {"question_set_state": "Generating"}
    rows = db.execute(select(QuestionSetJob).where(QuestionSetJob.candidate_id == candidate_id)).scalars().all()
    assert len(rows) == 1


def test_needs_review_outcome_is_ineligible_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="NeedsReview"
    )

    response = _post(client, token, candidate_id, "key-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_failed_outcome_is_ineligible_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="Failed"
    )

    response = _post(client, token, candidate_id, "key-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_cross_owner_candidate_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    owner_session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)
    revision_id = _seed_revision(db, owner_session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=owner_session_id, document_reference="D1", outcome="NewResult"
    )
    _, other_token = _admitted_identity_and_token(db)

    response = _post(client, other_token, candidate_id, "key-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_missing_candidate_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    response = _post(client, token, str(uuid.uuid4()), "key-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_generation_does_not_mutate_result_rank_or_shortlist(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)

    _post(client, token, candidate_id, "key-1")

    result = db.execute(select(CandidateResult).where(CandidateResult.candidate_id == candidate_id)).scalars().one()
    assert result.outcome == "NewResult"
    shortlist = db.execute(select(Shortlist).where(Shortlist.candidate_id == candidate_id)).scalars().one()
    assert shortlist.state == "NotShortlisted"
    assert shortlist.version == 1
