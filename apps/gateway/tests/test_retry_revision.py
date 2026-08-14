"""Story 5.3: the retry-revision constructor
(`POST /workspace/sessions/{id}/candidates/{id}/retry`) — one-per-Document
allowance, atomic construction of the next Analysis Revision with carried-
forward lineage-referenced results and exactly one fresh target job,
idempotent replay, and neutral no-op on every rejected case. Mirrors
test_results_endpoint.py's own-fixture/autouse-truncate live-DATABASE_URL
pattern.
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
)
from src.adapters.publication_coordinator import scan_and_publish

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


def _seed_candidate(
    db,
    *,
    revision_id: str,
    session_id: str,
    document_reference: str,
    outcome: str,
    headline_whole_percent: int | None = None,
    gate_codes: list | None = None,
    failure_category: str | None = None,
    proposal_items: list | None = None,
) -> dict[str, str]:
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
    db.commit()
    return {"candidate_id": candidate_id, "document_id": document_id, "candidate_job_id": candidate_job_id}


def _retry(client, token, session_id, candidate_id, *, expected_revision_number, idempotency_key):
    client.cookies.set("session", token)
    response = client.post(
        f"/workspace/sessions/{session_id}/candidates/{candidate_id}/retry",
        json={"expected_revision_number": expected_revision_number, "idempotency_key": idempotency_key},
    )
    client.cookies.clear()
    return response


def test_happy_path_creates_new_revision_with_carried_and_target_rows(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    published_at = datetime.now(timezone.utc)
    revision_id = _seed_revision(db, session_id, revision_number=1, published_at=published_at)

    ranked = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        headline_whole_percent=86,
        proposal_items=[{"job_requirement_id": "r1", "state": "Matched", "locator": "u1", "excerpt": "K8s"}],
    )
    needs_review = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2",
        outcome="NeedsReview", gate_codes=["TEXT_BELOW_500"],
    )
    failed = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D3",
        outcome="Failed", failure_category="Analysis timed out",
    )

    response = _retry(
        client, token, session_id, failed["candidate_id"],
        expected_revision_number=1, idempotency_key="retry-key-1",
    )
    assert response.status_code == 202
    body = response.json()
    assert body["retry_created"] is True
    assert body["current_revision_number"] == 2
    assert body["current_revision_published"] is False

    new_revision = (
        db.execute(
            select(AnalysisRevision.__table__)
            .where(AnalysisRevision.__table__.c.analysis_session_id == session_id)
            .where(AnalysisRevision.__table__.c.revision_number == 2)
        )
        .mappings()
        .one()
    )
    new_revision_id = new_revision["id"]
    assert new_revision["published_at"] is None

    memberships = {
        row["candidate_id"]: row
        for row in db.execute(
            select(RevisionMembership.__table__).where(
                RevisionMembership.__table__.c.analysis_revision_id == new_revision_id
            )
        )
        .mappings()
        .all()
    }
    assert len(memberships) == 3
    assert memberships[ranked["candidate_id"]]["outcome"] == "ReusedResult"
    assert memberships[needs_review["candidate_id"]]["outcome"] == "NeedsReview"
    assert memberships[failed["candidate_id"]]["outcome"] == "queued"

    jobs = {
        row["candidate_id"]: row
        for row in db.execute(
            select(CandidateJob.__table__).where(CandidateJob.__table__.c.analysis_revision_id == new_revision_id)
        )
        .mappings()
        .all()
    }
    assert len(jobs) == 3
    assert jobs[ranked["candidate_id"]]["status"] == "finalized"
    assert jobs[needs_review["candidate_id"]]["status"] == "finalized"
    assert jobs[failed["candidate_id"]]["status"] == "queued"

    results = {
        row["candidate_id"]: row
        for row in db.execute(
            select(CandidateResult.__table__).where(CandidateResult.__table__.c.analysis_revision_id == new_revision_id)
        )
        .mappings()
        .all()
    }
    assert set(results.keys()) == {ranked["candidate_id"], needs_review["candidate_id"]}
    assert results[ranked["candidate_id"]]["outcome"] == "ReusedResult"
    assert results[ranked["candidate_id"]]["candidate_job_id"] == ranked["candidate_job_id"]
    assert results[ranked["candidate_id"]]["headline_whole_percent"] == 86

    updated_document = (
        db.execute(select(Document.__table__).where(Document.__table__.c.id == failed["document_id"]))
        .mappings()
        .one()
    )
    assert updated_document["retried_into_revision_id"] == new_revision_id
    assert updated_document["retry_idempotency_key"] == "retry-key-1"

    # Prior published revision is byte-for-byte unchanged.
    prior_memberships = (
        db.execute(
            select(RevisionMembership.__table__).where(
                RevisionMembership.__table__.c.analysis_revision_id == revision_id
            )
        )
        .mappings()
        .all()
    )
    assert {row["candidate_id"]: row["outcome"] for row in prior_memberships} == {
        ranked["candidate_id"]: "NewResult",
        needs_review["candidate_id"]: "NeedsReview",
        failed["candidate_id"]: "Failed",
    }

    # AC#1's "retains the prior publication" — the current published
    # revision is still Revision 1 (Revision 2 has not published yet).
    client.cookies.set("session", token)
    results_response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()
    assert results_response.status_code == 200
    results_body = results_response.json()
    assert results_body["published"] is True
    assert results_body["revision_number"] == 1


def test_duplicate_replay_same_key_returns_same_revision(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    failed = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="Failed", failure_category="Analysis timed out",
    )

    first = _retry(client, token, session_id, failed["candidate_id"], expected_revision_number=1, idempotency_key="k1")
    second = _retry(client, token, session_id, failed["candidate_id"], expected_revision_number=1, idempotency_key="k1")

    assert first.status_code == 202 and second.status_code == 202
    assert first.json() == second.json()
    assert second.json()["retry_created"] is True

    revision_count = db.execute(
        select(AnalysisRevision.__table__.c.id).where(AnalysisRevision.__table__.c.analysis_session_id == session_id)
    ).all()
    assert len(revision_count) == 2  # source + exactly one retry revision, never two


def test_allowance_spent_different_key_returns_no_new_revision(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    failed = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="Failed", failure_category="Analysis timed out",
    )

    _retry(client, token, session_id, failed["candidate_id"], expected_revision_number=1, idempotency_key="k1")
    second = _retry(client, token, session_id, failed["candidate_id"], expected_revision_number=1, idempotency_key="k2")

    assert second.status_code == 202
    body = second.json()
    assert body["retry_created"] is False
    assert body["current_revision_number"] == 2

    revision_count = db.execute(
        select(AnalysisRevision.__table__.c.id).where(AnalysisRevision.__table__.c.analysis_session_id == session_id)
    ).all()
    assert len(revision_count) == 2  # no third revision created


def test_stale_expected_revision_number_returns_no_new_revision(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    failed = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="Failed", failure_category="Analysis timed out",
    )

    response = _retry(
        client, token, session_id, failed["candidate_id"], expected_revision_number=99, idempotency_key="k1"
    )
    assert response.status_code == 202
    body = response.json()
    assert body["retry_created"] is False
    assert body["current_revision_number"] == 1
    assert body["current_revision_published"] is True


def test_ineligible_needs_review_candidate_returns_no_new_revision(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    needs_review = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="NeedsReview", gate_codes=["TEXT_BELOW_500"],
    )

    response = _retry(
        client, token, session_id, needs_review["candidate_id"], expected_revision_number=1, idempotency_key="k1"
    )
    assert response.status_code == 202
    body = response.json()
    assert body["retry_created"] is False
    assert body["current_revision_number"] == 1


def test_cross_owner_candidate_returns_not_found(db):
    owner_identity, owner_token = _admitted_identity_and_token(db)
    other_identity, _ = _admitted_identity_and_token(db)
    session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    failed = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="Failed", failure_category="Analysis timed out",
    )

    other_session_id = _seed_session(db, other_identity.issuer, other_identity.subject)
    _seed_revision(db, other_session_id, published_at=datetime.now(timezone.utc))

    client.cookies.set("session", owner_token)
    response = client.post(
        f"/workspace/sessions/{other_session_id}/candidates/{failed['candidate_id']}/retry",
        json={"expected_revision_number": 1, "idempotency_key": "k1"},
    )
    client.cookies.clear()
    assert response.status_code == 404


def test_missing_session_returns_not_found(db):
    identity, token = _admitted_identity_and_token(db)
    response = _retry(
        client, token, str(uuid.uuid4()), str(uuid.uuid4()), expected_revision_number=1, idempotency_key="k1"
    )
    assert response.status_code == 404


def test_no_published_revision_yet_returns_neutral_noop(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, revision_number=1, published_at=None)
    unpublished = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1", outcome="queued"
    )

    response = _retry(
        client, token, session_id, unpublished["candidate_id"], expected_revision_number=1, idempotency_key="k1"
    )
    assert response.status_code == 202
    body = response.json()
    assert body["retry_created"] is False
    assert body["current_revision_number"] == 1
    assert body["current_revision_published"] is False


def test_second_concurrent_retry_of_different_failed_candidate_returns_neutral_noop_not_500(db):
    # Both retries compute revision_number relative to the same *published*
    # revision (Step 3 never looks at a concurrently-created unpublished
    # sibling) — a second retry of a *different* Failed Document before the
    # first retry's revision publishes must not 500 on
    # uq_analysis_revisions_session_number; it must return the same neutral
    # no-op every other conflict path returns, with its own allowance left
    # unconsumed (rolled back, not partially applied).
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    failed_one = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="Failed", failure_category="Analysis timed out",
    )
    failed_two = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2",
        outcome="Failed", failure_category="Analysis timed out",
    )

    first = _retry(
        client, token, session_id, failed_one["candidate_id"], expected_revision_number=1, idempotency_key="k1"
    )
    assert first.status_code == 202
    assert first.json() == {"retry_created": True, "current_revision_number": 2, "current_revision_published": False}

    second = _retry(
        client, token, session_id, failed_two["candidate_id"], expected_revision_number=1, idempotency_key="k2"
    )
    assert second.status_code == 202
    assert second.json()["retry_created"] is False
    assert second.json()["current_revision_number"] == 2

    revision_numbers = sorted(
        row[0]
        for row in db.execute(
            select(AnalysisRevision.__table__.c.revision_number).where(
                AnalysisRevision.__table__.c.analysis_session_id == session_id
            )
        ).all()
    )
    assert revision_numbers == [1, 2]  # exactly one retry revision, never a second colliding one

    second_document = (
        db.execute(select(Document.__table__).where(Document.__table__.c.id == failed_two["document_id"]))
        .mappings()
        .one()
    )
    assert second_document["retried_into_revision_id"] is None  # rolled back, allowance still unused


def test_reused_result_evidence_resolves_via_lineage_candidate_job_id_once_published(db):
    # Closes the Story 5.2 deferred-work item: no fixture had ever exercised
    # session_results's _proposal_items join for a ReusedResult Candidate.
    # Simulates the target retry finalizing (as candidate_finalizer.py
    # would) and publishing (as publication_coordinator.py would), then
    # confirms the carried-forward Candidate's strengths/gaps still resolve
    # through the OLD candidate_job_id — proving "reuse by reference" works
    # end-to-end without copying/mutating candidate_proposals (AR-12).
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    req_id = str(uuid.uuid4())
    db.add(
        JobRequirement(
            id=req_id,
            analysis_session_id=session_id,
            display_id="R1",
            component="mandatory_skills",
            classification="mandatory",
            canonical_text="Kubernetes operations",
            source_locators=[],
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    revision_id = _seed_revision(db, session_id, revision_number=1, published_at=datetime.now(timezone.utc))

    ranked = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="NewResult", headline_whole_percent=86,
        proposal_items=[{"job_requirement_id": req_id, "state": "Matched", "locator": "u1", "excerpt": "K8s"}],
    )
    failed = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2",
        outcome="Failed", failure_category="Analysis timed out",
    )

    retry_response = _retry(
        client, token, session_id, failed["candidate_id"], expected_revision_number=1, idempotency_key="k1"
    )
    assert retry_response.json()["retry_created"] is True
    new_revision_id = (
        db.execute(
            select(AnalysisRevision.__table__.c.id)
            .where(AnalysisRevision.__table__.c.analysis_session_id == session_id)
            .where(AnalysisRevision.__table__.c.revision_number == 2)
        )
        .scalars()
        .one()
    )

    # Simulate the worker + candidate_finalizer.py completing the retried
    # target (not this story's scope to actually run) so the new revision
    # becomes exact-terminal and publishable.
    db.execute(
        RevisionMembership.__table__.update()
        .where(RevisionMembership.__table__.c.analysis_revision_id == new_revision_id)
        .where(RevisionMembership.__table__.c.candidate_id == failed["candidate_id"])
        .values(outcome="NewResult")
    )
    db.execute(
        CandidateJob.__table__.update()
        .where(CandidateJob.__table__.c.analysis_revision_id == new_revision_id)
        .where(CandidateJob.__table__.c.candidate_id == failed["candidate_id"])
        .values(status="finalized")
    )
    db.add(
        CandidateResult(
            id=str(uuid.uuid4()),
            analysis_revision_id=new_revision_id,
            candidate_id=failed["candidate_id"],
            candidate_job_id=failed["candidate_job_id"],
            outcome="NewResult",
            created_at=datetime.now(timezone.utc),
            headline_whole_percent=70,
            overall_score_bps_numerator=Decimal(7000),
            overall_score_bps_denominator=Decimal(1),
            precise_score_percent=Decimal(70),
        )
    )
    db.execute(
        AnalysisRevision.__table__.update()
        .where(AnalysisRevision.__table__.c.id == new_revision_id)
        .values(requested_version=1)
    )
    db.commit()

    assert scan_and_publish(db) is True

    client.cookies.set("session", token)
    results_response = client.get(f"/workspace/sessions/{session_id}/results")
    client.cookies.clear()
    assert results_response.status_code == 200
    results_body = results_response.json()
    assert results_body["published"] is True
    assert results_body["revision_number"] == 2

    reused_row = next(row for row in results_body["ranked"] if row["candidate_id"] == ranked["candidate_id"])
    assert reused_row["headline_whole_percent"] == 86
    assert any("Kubernetes operations" in strength["requirement_text"] for strength in reused_row["strengths"])
