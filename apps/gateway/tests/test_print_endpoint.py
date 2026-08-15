"""Story 7.3: the authorized static print projection
(`GET /workspace/candidates/{candidate_id}/print/prepare` and
`GET /workspace/candidates/{candidate_id}/print`) — independent
re-authorization on each endpoint, scored-combined vs. report-only scope,
the Question-Set-Complete print gate, and the shared neutral 404 for
missing/cross-owner/stale-revision cases. Mirrors
test_candidate_report_endpoint.py's/test_candidate_reconciliation_endpoint.py's
own-fixture/autouse-truncate live-DATABASE_URL pattern.
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
    QuestionSetJob,
    QuestionSetVersion,
    RevisionMembership,
    ScoringConfiguration,
)

VALID_JOB_DESCRIPTION = "x" * 200
ALL_COMPONENTS = [
    "mandatory_skills",
    "relevant_experience",
    "responsibility_alignment",
    "preferred_skills_tools",
    "education_certifications",
    "domain_fit",
    "achievement_evidence_quality",
]
FIVE_CATEGORY_ITEMS = [
    {"number": 1, "category": "technical_functional", "text": "Q1", "source_requirement_id": ""},
    {"number": 2, "category": "experience_verification", "text": "Q2", "source_requirement_id": ""},
    {"number": 3, "category": "gap_focused", "text": "Q3", "source_requirement_id": ""},
    {"number": 4, "category": "behavioral", "text": "Q4", "source_requirement_id": ""},
    {"number": 5, "category": "follow_up", "text": "Q5", "source_requirement_id": ""},
    {"number": 6, "category": "technical_functional", "text": "Q6", "source_requirement_id": ""},
    {"number": 7, "category": "experience_verification", "text": "Q7", "source_requirement_id": ""},
    {"number": 8, "category": "gap_focused", "text": "Q8", "source_requirement_id": ""},
    {"number": 9, "category": "behavioral", "text": "Q9", "source_requirement_id": ""},
    {"number": 10, "category": "follow_up", "text": "Q10", "source_requirement_id": ""},
]

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
            "TRUNCATE TABLE sessions, users, question_set_versions, question_set_proposals, question_set_jobs, "
            "evidence_reviews, shortlists, candidate_proposals, candidate_results, candidate_identities, "
            "scoring_configurations, revision_memberships, candidate_jobs, job_requirements, candidates, "
            "documents, analysis_revisions, analysis_sessions CASCADE"
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


def _seed_scoring_configuration(db, session_id: str, applicable_weights: dict[str, int]) -> None:
    now = datetime.now(timezone.utc)
    for component in ALL_COMPONENTS:
        applicable = component in applicable_weights
        db.add(
            ScoringConfiguration(
                id=str(uuid.uuid4()),
                analysis_session_id=session_id,
                component=component,
                applicable=applicable,
                effective_weight_bps=applicable_weights.get(component, 0),
                created_at=now,
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
    display_name: str | None = None,
    headline_whole_percent: int | None = None,
    precise_score_percent: Decimal | None = None,
    component_contribution_display: dict | None = None,
    gate_codes: list | None = None,
    proposal_items: list | None = None,
) -> tuple[str, str, str]:
    """Returns (candidate_id, document_id, candidate_job_id)."""
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
    result_fields: dict = dict(headline_whole_percent=headline_whole_percent, gate_codes=gate_codes)
    if headline_whole_percent is not None:
        result_fields.update(
            overall_score_bps_numerator=Decimal(headline_whole_percent * 100),
            overall_score_bps_denominator=Decimal(1),
            precise_score_percent=precise_score_percent,
            component_contribution_display=component_contribution_display,
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
    return candidate_id, document_id, candidate_job_id


def _seed_published_question_set(db, *, candidate_id: str, revision_id: str) -> None:
    now = datetime.now(timezone.utc)
    job_id = str(uuid.uuid4())
    db.add(
        QuestionSetJob(
            id=job_id,
            candidate_id=candidate_id,
            analysis_revision_id=revision_id,
            status="published",
            idempotency_key=str(uuid.uuid4()),
            created_at=now,
        )
    )
    db.add(
        QuestionSetVersion(
            id=str(uuid.uuid4()),
            question_set_job_id=job_id,
            candidate_id=candidate_id,
            analysis_revision_id=revision_id,
            version=1,
            items_json=FIVE_CATEGORY_ITEMS,
            created_at=now,
        )
    )
    db.commit()


def _scored_candidate_with_complete_question_set(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    req_id = _seed_requirement(db, session_id, "MS-1", "mandatory_skills", "Kubernetes operations")
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id, _document_id, _job_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        display_name="Jordan Lee",
        headline_whole_percent=90,
        precise_score_percent=Decimal("90.00"),
        component_contribution_display={"mandatory_skills": "90.00"},
        proposal_items=[{"job_requirement_id": req_id, "state": "Matched", "locator": None, "excerpt": "K8s"}],
    )
    _seed_published_question_set(db, candidate_id=candidate_id, revision_id=revision_id)
    return token, candidate_id


def test_scored_complete_prepare_names_candidate_document_revision_and_is_unblocked(db):
    token, candidate_id = _scored_candidate_with_complete_question_set(db)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/print/prepare")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["candidate_id"] == candidate_id
    assert body["document_reference"] == "D1"
    assert body["original_filename"] == "D1.pdf"
    assert body["display_name"] == "Jordan Lee"
    assert body["revision_number"] == 1
    assert body["trigger"] == "Initial analysis"
    assert body["scope"] == "ScoredCombined"
    assert body["blocked"] is False
    assert "blocked_reason" not in body
    assert "notice" in body


def test_scored_complete_print_includes_reconciliation_evidence_and_all_ten_questions(db):
    token, candidate_id = _scored_candidate_with_complete_question_set(db)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/print")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["blocked"] is False
    assert body["scope"] == "ScoredCombined"
    assert len(body["evidence"]) == 1
    assert body["evidence"][0]["state"] == "Matched"
    assert body["reconciliation"]["headline_whole_percent"] == 90
    assert len(body["questions"]) == 10
    numbers = sorted(q["number"] for q in body["questions"])
    assert numbers == list(range(1, 11))
    categories = {q["category"] for q in body["questions"]}
    assert categories == {
        "technical_functional",
        "experience_verification",
        "gap_focused",
        "behavioral",
        "follow_up",
    }


@pytest.mark.parametrize("job_status", [None, "queued", "claimed", "failed"])
def test_scored_incomplete_question_set_blocks_both_endpoints(db, job_status):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id, _doc, _job = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D2",
        outcome="NewResult",
        headline_whole_percent=80,
        precise_score_percent=Decimal("80.00"),
        component_contribution_display={"mandatory_skills": "80.00"},
    )
    if job_status is not None:
        db.add(
            QuestionSetJob(
                id=str(uuid.uuid4()),
                candidate_id=candidate_id,
                analysis_revision_id=revision_id,
                status=job_status,
                idempotency_key=str(uuid.uuid4()),
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    client.cookies.set("session", token)
    prepare_response = client.get(f"/workspace/candidates/{candidate_id}/print/prepare")
    print_response = client.get(f"/workspace/candidates/{candidate_id}/print")
    client.cookies.clear()

    assert prepare_response.json()["blocked"] is True
    assert prepare_response.json()["blocked_reason"] == "question_set_incomplete"
    print_body = print_response.json()
    assert print_body["blocked"] is True
    assert "evidence" not in print_body
    assert "reconciliation" not in print_body
    assert "questions" not in print_body


def test_needs_review_print_has_gate_codes_and_evidence_but_no_score_or_questions(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    req_id = _seed_requirement(db, session_id, "MS-1", "mandatory_skills", "Kubernetes operations")
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id, _doc, _job = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D3",
        outcome="NeedsReview",
        gate_codes=["TEXT_BELOW_500"],
    )

    client.cookies.set("session", token)
    prepare_response = client.get(f"/workspace/candidates/{candidate_id}/print/prepare")
    print_response = client.get(f"/workspace/candidates/{candidate_id}/print")
    client.cookies.clear()

    assert prepare_response.json()["scope"] == "ReportOnly"
    assert prepare_response.json()["blocked"] is False

    body = print_response.json()
    assert body["scope"] == "ReportOnly"
    assert body["blocked"] is False
    assert body["gate_codes"] == ["TEXT_BELOW_500"]
    assert "evidence" in body
    assert "reconciliation" not in body
    assert "questions" not in body


def test_retry_revision_trigger_names_the_retried_document(db):
    """AD-5: Candidate/Document are stable across revisions — a retry-in-new-
    revision reuses the same Candidate/Document row and adds a second
    RevisionMembership, it never creates a second Document with the same
    reference (that would violate the session-scoped uniqueness constraint)."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_1_id = _seed_revision(db, session_id, revision_number=1, published_at=datetime.now(timezone.utc))
    candidate_id, document_id, _job = _seed_candidate(
        db,
        revision_id=revision_1_id,
        session_id=session_id,
        document_reference="A7K2",
        outcome="Failed",
    )
    revision_2_id = _seed_revision(db, session_id, revision_number=2, published_at=datetime.now(timezone.utc))
    now = datetime.now(timezone.utc)
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_2_id,
            candidate_id=candidate_id,
            outcome="NewResult",
            created_at=now,
        )
    )
    candidate_job_id = str(uuid.uuid4())
    db.add(
        CandidateJob(
            id=candidate_job_id,
            analysis_revision_id=revision_2_id,
            candidate_id=candidate_id,
            status="finalized",
            created_at=now,
        )
    )
    db.add(
        CandidateResult(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_2_id,
            candidate_id=candidate_id,
            candidate_job_id=candidate_job_id,
            outcome="NewResult",
            created_at=now,
            headline_whole_percent=75,
            overall_score_bps_numerator=Decimal(7500),
            overall_score_bps_denominator=Decimal(1),
            precise_score_percent=Decimal("75.00"),
            component_contribution_display={"mandatory_skills": "75.00"},
        )
    )
    db.execute(
        text("UPDATE documents SET retried_into_revision_id = :rid WHERE id = :did"),
        {"rid": revision_2_id, "did": document_id},
    )
    db.commit()

    client.cookies.set("session", token)
    response = client.get(
        f"/workspace/candidates/{candidate_id}/print/prepare", params={"revision_number": 2}
    )
    client.cookies.clear()

    assert response.json()["trigger"] == "Retry of Document A7K2"


def test_scored_candidate_with_no_component_contribution_display_is_the_neutral_404(db):
    """Code review addition (Blind Hunter): the Dev Notes call the
    reconciliation-branch guards this story's genuinely new risk surface,
    but no test previously proved `_load_reconciliation_payload`'s
    `component_contribution_display is None` guard is reachable through
    `/print` rather than crashing. A `NewResult` membership with a scored
    headline but no persisted `component_contribution_display` is a
    data-integrity edge case the guard exists to catch."""
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id, _doc, _job = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D4",
        outcome="NewResult",
        headline_whole_percent=88,
        precise_score_percent=Decimal("88.00"),
        component_contribution_display=None,
    )
    _seed_published_question_set(db, candidate_id=candidate_id, revision_id=revision_id)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/print")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_missing_candidate_id_is_the_same_neutral_404_on_both_endpoints(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    client.cookies.set("session", token)
    prepare_response = client.get(f"/workspace/candidates/{uuid.uuid4()}/print/prepare")
    print_response = client.get(f"/workspace/candidates/{uuid.uuid4()}/print")
    client.cookies.clear()

    assert prepare_response.status_code == 404
    assert prepare_response.json() == {"detail": "Not found"}
    assert print_response.status_code == 404
    assert print_response.json() == {"detail": "Not found"}


def test_cross_owner_candidate_is_the_same_neutral_404_on_both_endpoints(db):
    token, candidate_id = _scored_candidate_with_complete_question_set(db)
    _, other_token = _admitted_identity_and_token(db)

    client.cookies.set("session", other_token)
    prepare_response = client.get(f"/workspace/candidates/{candidate_id}/print/prepare")
    print_response = client.get(f"/workspace/candidates/{candidate_id}/print")
    client.cookies.clear()

    assert prepare_response.status_code == 404
    assert print_response.status_code == 404


def test_stale_or_nonexistent_revision_number_is_the_same_neutral_404(db):
    token, candidate_id = _scored_candidate_with_complete_question_set(db)

    client.cookies.set("session", token)
    # Code review fix (Acceptance Auditor/Blind Hunter, convergent): the
    # original version of this test only hit `/print`, silently leaving
    # `/print/prepare`'s independent authorization path unverified against
    # a stale revision_number despite Task 2(e)'s explicit "not one shared
    # setup call proving only one endpoint" instruction.
    unpublished_prepare = client.get(
        f"/workspace/candidates/{candidate_id}/print/prepare", params={"revision_number": 999}
    )
    unpublished_print = client.get(
        f"/workspace/candidates/{candidate_id}/print", params={"revision_number": 999}
    )
    client.cookies.clear()

    assert unpublished_prepare.status_code == 404
    assert unpublished_print.status_code == 404


def test_malformed_or_oversized_candidate_id_is_the_same_neutral_404_on_both_endpoints(db):
    """Code review fix (Acceptance Auditor): Task 2(e) explicitly required a
    malformed/oversized id case; the prior test suite only covered a
    well-formed-but-nonexistent UUID, not an over-_MAX_ID_LENGTH or
    non-UUID string."""
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)
    oversized_id = "x" * 100

    client.cookies.set("session", token)
    prepare_response = client.get(f"/workspace/candidates/{oversized_id}/print/prepare")
    print_response = client.get(f"/workspace/candidates/{oversized_id}/print")
    client.cookies.clear()

    assert prepare_response.status_code == 404
    assert prepare_response.json() == {"detail": "Not found"}
    assert print_response.status_code == 404
    assert print_response.json() == {"detail": "Not found"}


def test_print_does_not_mutate_evidence_review_or_question_set_state(db):
    token, candidate_id = _scored_candidate_with_complete_question_set(db)

    before_reviews = db.execute(text("SELECT COUNT(*) FROM evidence_reviews")).scalar_one()
    before_versions = db.execute(text("SELECT COUNT(*) FROM question_set_versions")).scalar_one()

    client.cookies.set("session", token)
    client.get(f"/workspace/candidates/{candidate_id}/print/prepare")
    client.get(f"/workspace/candidates/{candidate_id}/print")
    client.cookies.clear()

    after_reviews = db.execute(text("SELECT COUNT(*) FROM evidence_reviews")).scalar_one()
    after_versions = db.execute(text("SELECT COUNT(*) FROM question_set_versions")).scalar_one()
    assert before_reviews == after_reviews
    assert before_versions == after_versions
