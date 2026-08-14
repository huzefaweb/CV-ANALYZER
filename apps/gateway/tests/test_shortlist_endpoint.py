"""Story 6.4: the authorized Candidate-owned Shortlist mutation
(`PUT /workspace/candidates/{candidate_id}/shortlist`). Mirrors
test_evidence_review_endpoint.py's own-fixture/autouse-truncate live-
DATABASE_URL pattern.
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


def _seed_candidate_membership(db, *, revision_id: str, session_id: str, candidate_id: str, document_id: str, document_reference: str, outcome: str) -> None:
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
    result_fields = dict(headline_whole_percent=70 if outcome != "NeedsReview" and outcome != "Failed" else None)
    if outcome not in ("NeedsReview", "Failed"):
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
    db.commit()


def _seed_candidate(
    db,
    *,
    revision_id: str,
    session_id: str,
    document_reference: str,
    outcome: str,
    shortlisted: bool = False,
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
    db.commit()
    _seed_candidate_membership(
        db,
        revision_id=revision_id,
        session_id=session_id,
        candidate_id=candidate_id,
        document_id=document_id,
        document_reference=document_reference,
        outcome=outcome,
    )
    # Mirrors candidate_finalizer.py's default-insert-on-every-outcome-branch
    # (Story 4.6) — every finalized Candidate has exactly one shortlists row
    # before this endpoint is ever reachable.
    db.add(
        Shortlist(
            id=str(uuid.uuid4()),
            candidate_id=candidate_id,
            state="Shortlisted" if shortlisted else "NotShortlisted",
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return candidate_id


def _seed_ranked_candidate(db, *, document_reference="D1", revision_number=1, shortlisted=False):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(
        db, session_id, revision_number=revision_number, published_at=datetime.now(timezone.utc)
    )
    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference=document_reference,
        outcome="NewResult",
        shortlisted=shortlisted,
    )
    return identity, token, session_id, revision_id, candidate_id


def _put(client, token, candidate_id, state, expected_version, idempotency_key, revision_number=None):
    client.cookies.set("session", token)
    url = f"/workspace/candidates/{candidate_id}/shortlist"
    if revision_number is not None:
        url += f"?revision_number={revision_number}"
    response = client.put(
        url,
        json={"state": state, "expected_version": expected_version, "idempotency_key": idempotency_key},
    )
    client.cookies.clear()
    return response


def test_first_mutation_bumps_version_from_the_finalizer_default(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)

    response = _put(client, token, candidate_id, "Shortlisted", 1, "key-1")

    assert response.status_code == 200
    assert response.json() == {"state": "Shortlisted", "version": 2}


def test_toggle_back_and_forth_increments_version_each_real_transition(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)

    r1 = _put(client, token, candidate_id, "Shortlisted", 1, "key-1")
    assert r1.json() == {"state": "Shortlisted", "version": 2}

    r2 = _put(client, token, candidate_id, "NotShortlisted", 2, "key-2")
    assert r2.json() == {"state": "NotShortlisted", "version": 3}


def test_idempotent_replay_returns_current_state_without_bumping_version(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)
    _put(client, token, candidate_id, "Shortlisted", 1, "key-1")

    replay = _put(client, token, candidate_id, "Shortlisted", 1, "key-1")

    assert replay.status_code == 200
    assert replay.json() == {"state": "Shortlisted", "version": 2}


def test_reusing_an_idempotency_key_with_a_different_state_is_a_conflict_not_a_silent_apply(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)
    first = _put(client, token, candidate_id, "Shortlisted", 1, "reused-key")
    assert first.status_code == 200

    conflict = _put(client, token, candidate_id, "NotShortlisted", 2, "reused-key")

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"state": "Shortlisted", "version": 2}


def test_stale_expected_version_returns_409_with_no_persisted_change(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)
    _put(client, token, candidate_id, "Shortlisted", 1, "key-1")

    conflict = _put(client, token, candidate_id, "NotShortlisted", 1, "key-2")

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"state": "Shortlisted", "version": 2}
    row = db.execute(select(Shortlist).where(Shortlist.candidate_id == candidate_id)).scalars().one()
    assert row.state == "Shortlisted"
    assert row.version == 2


def test_concurrent_toggle_requests_exactly_one_wins(db):
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)

    def attempt(key):
        return _put(TestClient(app), token, candidate_id, "Shortlisted", 1, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(attempt, ["key-a", "key-b"]))

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 409]
    winner = r1 if r1.status_code == 200 else r2
    assert winner.json() == {"state": "Shortlisted", "version": 2}


def test_concurrent_identical_requests_the_loser_detects_its_own_key_and_replays_not_conflicts(db):
    """Code review finding (Blind Hunter): test_concurrent_toggle_requests_
    exactly_one_wins only exercises two DIFFERENT idempotency keys racing —
    it never proves the actual "duplicate in-flight request with the same
    key" scenario the loser-re-reads-and-detects-its-own-key branch
    (workspace.py's `current["last_command_idempotency_key"] ==
    body.idempotency_key` check after a 0-row CAS) exists to handle. Two
    threads submit the byte-identical command (same state, same
    expected_version, same idempotency_key) concurrently: exactly one
    performs the real CAS UPDATE, the other's UPDATE affects 0 rows and its
    re-read finds its own key already committed — a true-concurrency
    replay, not a stale-version conflict, so BOTH requests must return 200
    with the identical committed state, never a 409."""
    _, token, _, _, candidate_id = _seed_ranked_candidate(db)

    def attempt(_):
        return _put(TestClient(app), token, candidate_id, "Shortlisted", 1, "same-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(attempt, [0, 1]))

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == {"state": "Shortlisted", "version": 2}
    assert r2.json() == {"state": "Shortlisted", "version": 2}
    row = db.execute(select(Shortlist).where(Shortlist.candidate_id == candidate_id)).scalars().one()
    assert row.state == "Shortlisted"
    assert row.version == 2


def test_shortlist_toggle_succeeds_on_a_failed_candidate_no_outcome_gating(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="Failed",
    )

    response = _put(client, token, candidate_id, "Shortlisted", 1, "key-1")

    assert response.status_code == 200
    assert response.json() == {"state": "Shortlisted", "version": 2}


def test_cross_owner_candidate_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    owner_session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)
    revision_id = _seed_revision(db, owner_session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=owner_session_id, document_reference="D1", outcome="NewResult",
    )
    _, other_token = _admitted_identity_and_token(db)

    response = _put(client, other_token, candidate_id, "Shortlisted", 1, "key-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_missing_candidate_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    response = _put(client, token, str(uuid.uuid4()), "Shortlisted", 1, "key-1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_retry_to_needs_review_or_failed_retains_prior_shortlist_state(db):
    """AC#3: a retry that moves a previously ranked Candidate to a new
    outcome must not disturb its existing Shortlist state — proven here by
    directly asserting the single shared shortlists row (Story 4.6's
    Candidate-owned, not revision-owned, design) is unaffected by a second
    revision's membership existing with a different outcome."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_1_id = _seed_revision(db, session_id, revision_number=1, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_1_id, session_id=session_id, document_reference="D1", outcome="NewResult",
    )

    shortlisted = _put(client, token, candidate_id, "Shortlisted", 1, "key-1", revision_number=1)
    assert shortlisted.status_code == 200

    revision_2_id = _seed_revision(db, session_id, revision_number=2, published_at=datetime.now(timezone.utc))
    _seed_candidate_membership(
        db,
        revision_id=revision_2_id,
        session_id=session_id,
        candidate_id=candidate_id,
        document_id=str(uuid.uuid4()),
        document_reference="D1",
        outcome="Failed",
    )

    row = db.execute(select(Shortlist).where(Shortlist.candidate_id == candidate_id)).scalars().one()
    assert row.state == "Shortlisted"
    assert row.version == 2

    still_shortlisted = _put(client, token, candidate_id, "NotShortlisted", 2, "key-2", revision_number=2)
    assert still_shortlisted.status_code == 200
    assert still_shortlisted.json() == {"state": "NotShortlisted", "version": 3}
