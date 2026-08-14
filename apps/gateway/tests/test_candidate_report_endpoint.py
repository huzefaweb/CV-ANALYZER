"""Story 6.1: the authorized single-Candidate Report projection
(`GET /workspace/candidates/{candidate_id}/report`) — two-hop ownership
(Candidate -> Analysis Session -> creator), revision-specific score-or-
Needs-Review content, and the shared neutral 404 for Failed/missing/
cross-owner/stale-revision cases. Mirrors test_results_endpoint.py's own-
fixture/autouse-truncate live-DATABASE_URL pattern.
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
    headline_whole_percent: int | None = None,
    precise_score_percent: Decimal | None = None,
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
            precise_score_percent=precise_score_percent
            if precise_score_percent is not None
            else Decimal(headline_whole_percent),
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


def test_ranked_report_includes_score_findings_interview_focus_and_shortlist(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    matched_req = _seed_requirement(db, session_id, "R1", "mandatory_skills", "Kubernetes operations")
    gap_req = _seed_requirement(db, session_id, "R2", "domain_fit", "Kafka production operations")
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))

    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        display_name="Jordan Lee",
        headline_whole_percent=86,
        precise_score_percent=Decimal("85.63"),
        proposal_items=[
            {"job_requirement_id": matched_req, "state": "Matched", "locator": "u1", "excerpt": "K8s"},
            {"job_requirement_id": gap_req, "state": "Not Found", "locator": None, "excerpt": None},
        ],
        shortlist_state="Shortlisted",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/report")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["candidate_id"] == candidate_id
    assert body["analysis_session_id"] == session_id
    assert body["display_name"] == "Jordan Lee"
    assert body["outcome"] == "Ranked"
    assert body["headline_whole_percent"] == 86
    assert body["precise_score_percent"] == "85.63"
    assert body["shortlist_state"] == "Shortlisted"
    assert body["strengths"] == [{"requirement_text": "Kubernetes operations", "state": "Matched"}]
    assert body["gaps"] == [{"requirement_text": "Kafka production operations", "state": "Not Found"}]
    assert body["interview_focus"] == body["gaps"]
    assert "gate_codes" not in body
    assert "notice" in body
    assert "revision_created_at" in body and body["revision_created_at"]


def test_needs_review_report_suppresses_score_and_exposes_gate_codes(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))

    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D2",
        outcome="NeedsReview",
        gate_codes=["TEXT_BELOW_500"],
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/report")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "NeedsReview"
    assert body["gate_codes"] == ["TEXT_BELOW_500"]
    assert "headline_whole_percent" not in body
    assert "precise_score_percent" not in body


def test_failed_candidate_has_no_report(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D3",
        outcome="Failed",
        failure_category="Analysis timed out",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/report")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_missing_candidate_id_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{uuid.uuid4()}/report")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_cross_owner_candidate_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    owner_session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)
    revision_id = _seed_revision(db, owner_session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=owner_session_id, document_reference="D1",
        outcome="NewResult", headline_whole_percent=70,
    )

    _, other_token = _admitted_identity_and_token(db)

    client.cookies.set("session", other_token)
    response = client.get(f"/workspace/candidates/{candidate_id}/report")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_oversized_candidate_id_is_neutral_404_without_reaching_postgres(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{'x' * 100}/report")
    client.cookies.clear()

    assert response.status_code == 404


def test_revision_number_targets_an_older_published_revision(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    older_revision_id = _seed_revision(db, session_id, revision_number=1, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=older_revision_id, session_id=session_id, document_reference="D1",
        outcome="NewResult", headline_whole_percent=55, precise_score_percent=Decimal("55.00"),
    )
    current_revision_id = _seed_revision(db, session_id, revision_number=2, published_at=datetime.now(timezone.utc))
    # Same Candidate, reused into the newer revision with a different score —
    # proves the endpoint reads the *requested* revision's own result row.
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()), analysis_revision_id=current_revision_id, candidate_id=candidate_id,
            outcome="ReusedResult", created_at=datetime.now(timezone.utc),
        )
    )
    reused_job_id = str(uuid.uuid4())
    db.add(
        CandidateJob(
            id=reused_job_id, analysis_revision_id=current_revision_id, candidate_id=candidate_id,
            status="finalized", created_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        CandidateResult(
            id=str(uuid.uuid4()), analysis_revision_id=current_revision_id, candidate_id=candidate_id,
            candidate_job_id=reused_job_id, outcome="ReusedResult", created_at=datetime.now(timezone.utc),
            headline_whole_percent=91, precise_score_percent=Decimal("90.50"),
            overall_score_bps_numerator=Decimal(9100), overall_score_bps_denominator=Decimal(1),
        )
    )
    db.commit()

    client.cookies.set("session", token)
    older_response = client.get(f"/workspace/candidates/{candidate_id}/report", params={"revision_number": 1})
    current_response = client.get(f"/workspace/candidates/{candidate_id}/report", params={"revision_number": 2})
    default_response = client.get(f"/workspace/candidates/{candidate_id}/report")
    client.cookies.clear()

    older_body = older_response.json()
    assert older_body["revision_number"] == 1
    assert older_body["is_current"] is False
    assert older_body["headline_whole_percent"] == 55

    current_body = current_response.json()
    assert current_body["revision_number"] == 2
    assert current_body["is_current"] is True
    assert current_body["headline_whole_percent"] == 91

    assert default_response.json() == current_body


def test_stale_or_nonexistent_revision_number_is_the_same_neutral_404(db):
    """A `revision_number` that exists but isn't published, and one that
    doesn't exist for this session at all, both collapse into the same
    neutral 404 as every other unavailable case (AC#3) — a caller cannot
    distinguish "not published yet" from "doesn't exist" from "wrong
    session"."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, revision_number=1, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="NewResult", headline_whole_percent=70,
    )
    _seed_revision(db, session_id, revision_number=2, published_at=None)  # still processing

    client.cookies.set("session", token)
    unpublished_response = client.get(f"/workspace/candidates/{candidate_id}/report", params={"revision_number": 2})
    nonexistent_response = client.get(f"/workspace/candidates/{candidate_id}/report", params={"revision_number": 999})
    client.cookies.clear()

    assert unpublished_response.status_code == 404
    assert unpublished_response.json() == {"detail": "Not found"}
    assert nonexistent_response.status_code == 404
    assert nonexistent_response.json() == {"detail": "Not found"}


def test_malformed_proposal_item_is_neutral_404_not_a_500(db):
    """A non-Mapping entry in `items_json` (corrupted/legacy data) raises
    `TypeError` from `summarize_evidence`'s `item.get(...)`/`item[...]`
    access — not `KeyError`/`ValueError` — and must still collapse into the
    same neutral 404 as any other anomalous row, not leak as a 500."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="NewResult", headline_whole_percent=70,
        proposal_items=["not-a-mapping"],
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/report")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_candidate_with_no_identity_row_falls_back_to_document_reference(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D7K2",
        outcome="NewResult", headline_whole_percent=70,
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/report")
    client.cookies.clear()

    assert response.json()["display_name"] == "D7K2"
