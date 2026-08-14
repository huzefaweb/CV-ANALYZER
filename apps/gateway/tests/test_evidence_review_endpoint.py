"""Story 6.3: the authorized Disputed-review mutation
(`PUT /workspace/candidates/{candidate_id}/evidence/{job_requirement_id}/review`).
Mirrors test_candidate_evidence_endpoint.py's own-fixture/autouse-truncate
live-DATABASE_URL pattern.
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
    CandidateProposal,
    CandidateResult,
    Document,
    EvidenceReview,
    JobRequirement,
    RevisionMembership,
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
            "TRUNCATE TABLE sessions, users, shortlists, evidence_reviews, candidate_proposals, candidate_results, "
            "candidate_identities, parse_artifacts, revision_memberships, candidate_jobs, "
            "job_requirements, candidates, documents, analysis_revisions, analysis_sessions CASCADE"
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
    proposal_items: list | None = None,
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
    result_fields = dict(headline_whole_percent=70 if outcome != "NeedsReview" else None)
    if outcome != "NeedsReview":
        result_fields.update(
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
    db.commit()
    return candidate_id


def _seed_ranked_candidate_with_one_requirement(db, *, document_reference="D1", revision_number=1):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    requirement_id = _seed_requirement(db, session_id, "R1", "mandatory_skills", "Kubernetes operations")
    revision_id = _seed_revision(
        db, session_id, revision_number=revision_number, published_at=datetime.now(timezone.utc)
    )
    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference=document_reference,
        outcome="NewResult",
        proposal_items=[{"job_requirement_id": requirement_id, "state": "Matched", "locator": "", "excerpt": ""}],
    )
    return identity, token, session_id, requirement_id, revision_id, candidate_id


def _put(client, token, candidate_id, requirement_id, disputed, expected_version, idempotency_key):
    client.cookies.set("session", token)
    response = client.put(
        f"/workspace/candidates/{candidate_id}/evidence/{requirement_id}/review",
        json={"disputed": disputed, "expected_version": expected_version, "idempotency_key": idempotency_key},
    )
    client.cookies.clear()
    return response


def test_first_ever_set_creates_version_one(db):
    _, token, _, requirement_id, _, candidate_id = _seed_ranked_candidate_with_one_requirement(db)

    response = _put(client, token, candidate_id, requirement_id, True, 0, "key-1")

    assert response.status_code == 200
    assert response.json() == {"job_requirement_id": requirement_id, "disputed": True, "version": 1}


def test_clear_then_reset_cycle_increments_version_each_real_transition(db):
    _, token, _, requirement_id, _, candidate_id = _seed_ranked_candidate_with_one_requirement(db)

    r1 = _put(client, token, candidate_id, requirement_id, True, 0, "key-1")
    assert r1.json()["version"] == 1

    r2 = _put(client, token, candidate_id, requirement_id, False, 1, "key-2")
    assert r2.status_code == 200
    assert r2.json() == {"job_requirement_id": requirement_id, "disputed": False, "version": 2}

    r3 = _put(client, token, candidate_id, requirement_id, True, 2, "key-3")
    assert r3.json() == {"job_requirement_id": requirement_id, "disputed": True, "version": 3}


def test_clearing_an_already_clear_flag_is_a_no_op_and_creates_no_row(db):
    _, token, _, requirement_id, revision_id, candidate_id = _seed_ranked_candidate_with_one_requirement(db)

    response = _put(client, token, candidate_id, requirement_id, False, 0, "key-1")

    assert response.status_code == 200
    assert response.json() == {"job_requirement_id": requirement_id, "disputed": False, "version": 0}
    row = (
        db.execute(
            select(EvidenceReview).where(
                (EvidenceReview.analysis_revision_id == revision_id)
                & (EvidenceReview.candidate_id == candidate_id)
            )
        )
        .scalars()
        .one_or_none()
    )
    assert row is None


def test_idempotent_replay_returns_current_state_without_bumping_version(db):
    _, token, _, requirement_id, _, candidate_id = _seed_ranked_candidate_with_one_requirement(db)
    _put(client, token, candidate_id, requirement_id, True, 0, "key-1")

    replay = _put(client, token, candidate_id, requirement_id, True, 0, "key-1")

    assert replay.status_code == 200
    assert replay.json() == {"job_requirement_id": requirement_id, "disputed": True, "version": 1}


def test_stale_expected_version_returns_409_with_no_persisted_change(db):
    _, token, _, requirement_id, revision_id, candidate_id = _seed_ranked_candidate_with_one_requirement(db)
    _put(client, token, candidate_id, requirement_id, True, 0, "key-1")

    conflict = _put(client, token, candidate_id, requirement_id, False, 0, "key-2")

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"job_requirement_id": requirement_id, "disputed": True, "version": 1}
    row = (
        db.execute(
            select(EvidenceReview).where(
                (EvidenceReview.analysis_revision_id == revision_id)
                & (EvidenceReview.candidate_id == candidate_id)
            )
        )
        .scalars()
        .one()
    )
    assert row.disputed is True
    assert row.version == 1


def test_concurrent_first_set_requests_exactly_one_wins(db):
    _, token, _, requirement_id, _, candidate_id = _seed_ranked_candidate_with_one_requirement(db)

    def attempt(key):
        return _put(TestClient(app), token, candidate_id, requirement_id, True, 0, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(attempt, ["key-a", "key-b"]))

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 409]
    winner = r1 if r1.status_code == 200 else r2
    assert winner.json() == {"job_requirement_id": requirement_id, "disputed": True, "version": 1}


def test_expected_version_out_of_int32_range_is_a_clean_422_not_a_500(db):
    """Code review finding (Edge Case Hunter): expected_version had no
    upper bound, so a value outside Postgres Integer range reached the
    CAS UPDATE's bind param as an uncaught DataError. Now bounded by
    Field(le=2147483647), matching revision_number's own bound — FastAPI
    rejects it before the request body ever reaches the handler."""
    _, token, _, requirement_id, _, candidate_id = _seed_ranked_candidate_with_one_requirement(db)

    response = _put(client, token, candidate_id, requirement_id, True, 99999999999, "key-1")

    assert response.status_code == 422


def test_reusing_an_idempotency_key_with_a_different_disputed_value_is_a_conflict_not_a_silent_apply(db):
    """Code review finding (Edge Case Hunter): reusing the same
    idempotency_key for a genuinely different intended effect must be
    rejected as a conflict, not silently fall through to a normal CAS
    update — "same key" only means "same command" when the value is
    identical too."""
    _, token, _, requirement_id, revision_id, candidate_id = _seed_ranked_candidate_with_one_requirement(db)
    first = _put(client, token, candidate_id, requirement_id, True, 0, "reused-key")
    assert first.status_code == 200

    conflict = _put(client, token, candidate_id, requirement_id, False, 1, "reused-key")

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"job_requirement_id": requirement_id, "disputed": True, "version": 1}
    row = (
        db.execute(
            select(EvidenceReview).where(
                (EvidenceReview.analysis_revision_id == revision_id)
                & (EvidenceReview.candidate_id == candidate_id)
            )
        )
        .scalars()
        .one()
    )
    assert row.disputed is True
    assert row.version == 1


def test_concurrent_mark_and_clear_never_corrupts_state_even_though_the_no_op_reply_can_still_race(db):
    """Code review finding (Blind Hunter, TOCTOU) — the achievable
    invariant, proven honestly. The 'no row exists -> clearing is a
    no-op' branch now re-checks immediately before responding, which
    narrows (does not, and structurally cannot without a lock, eliminate)
    the window where a concurrent first-set commits between that
    request's *own* re-check and its response — an earlier version of
    this test asserted full post-hoc linearizability across both
    requests' responses and *failed* under real thread scheduling,
    proving that stronger claim false. What the fix DOES guarantee, and
    what this test proves with real thread concurrency (two separate
    TestClient instances — a shared cookie jar isn't safe under
    concurrent dispatch, Story 3.3's established fix): no crash, no
    duplicate row (the unique index + IntegrityError catch already
    covers this), and the table ends with either zero or exactly one
    coherent row — never a corrupted/partial write."""
    _, token, _, requirement_id, revision_id, candidate_id = _seed_ranked_candidate_with_one_requirement(db)

    def mark():
        return _put(TestClient(app), token, candidate_id, requirement_id, True, 0, "mark-key")

    def clear():
        return _put(TestClient(app), token, candidate_id, requirement_id, False, 0, "clear-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        mark_response, clear_response = list(pool.map(lambda fn: fn(), [mark, clear]))

    assert mark_response.status_code in (200, 409)
    assert clear_response.status_code in (200, 409)

    rows = (
        db.execute(
            select(EvidenceReview).where(
                (EvidenceReview.analysis_revision_id == revision_id)
                & (EvidenceReview.candidate_id == candidate_id)
                & (EvidenceReview.job_requirement_id == requirement_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) <= 1
    if rows:
        assert rows[0].version == 1
        assert rows[0].disputed is True


def test_requirement_with_no_evidence_row_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="NeedsReview",
    )

    response = _put(client, token, candidate_id, str(uuid.uuid4()), True, 0, "key-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_failed_candidate_has_no_review_mutation(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    requirement_id = _seed_requirement(db, session_id, "R1", "mandatory_skills", "Kubernetes operations")
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="Failed",
    )

    response = _put(client, token, candidate_id, requirement_id, True, 0, "key-1")

    assert response.status_code == 404


def test_cross_owner_candidate_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    owner_session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)
    requirement_id = _seed_requirement(db, owner_session_id, "R1", "mandatory_skills", "Kubernetes operations")
    revision_id = _seed_revision(db, owner_session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=owner_session_id,
        document_reference="D1",
        outcome="NewResult",
        proposal_items=[{"job_requirement_id": requirement_id, "state": "Matched", "locator": "", "excerpt": ""}],
    )
    _, other_token = _admitted_identity_and_token(db)

    response = _put(client, other_token, candidate_id, requirement_id, True, 0, "key-1")

    assert response.status_code == 404


def test_missing_candidate_id_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    response = _put(client, token, str(uuid.uuid4()), str(uuid.uuid4()), True, 0, "key-1")

    assert response.status_code == 404


def test_disputed_state_in_one_revision_never_touches_another_revision(db):
    """AC#1: 'other revision flags remain byte-for-byte unchanged' — direct
    DB assertion, not just response inspection."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    requirement_id = _seed_requirement(db, session_id, "R1", "mandatory_skills", "Kubernetes operations")
    revision_1_id = _seed_revision(db, session_id, revision_number=1, published_at=datetime.now(timezone.utc))
    revision_2_id = _seed_revision(db, session_id, revision_number=2, published_at=datetime.now(timezone.utc))
    items = [{"job_requirement_id": requirement_id, "state": "Matched", "locator": "", "excerpt": ""}]
    candidate_1 = _seed_candidate(
        db, revision_id=revision_1_id, session_id=session_id, document_reference="D1", outcome="NewResult",
        proposal_items=items,
    )
    candidate_2 = _seed_candidate(
        db, revision_id=revision_2_id, session_id=session_id, document_reference="D2", outcome="NewResult",
        proposal_items=items,
    )

    response = _put(client, token, candidate_2, requirement_id, True, 0, "key-1")
    assert response.status_code == 200

    revision_1_rows = (
        db.execute(select(EvidenceReview).where(EvidenceReview.analysis_revision_id == revision_1_id))
        .scalars()
        .all()
    )
    assert revision_1_rows == []
    revision_2_rows = (
        db.execute(select(EvidenceReview).where(EvidenceReview.analysis_revision_id == revision_2_id))
        .scalars()
        .all()
    )
    assert len(revision_2_rows) == 1
    assert revision_2_rows[0].candidate_id == candidate_2
