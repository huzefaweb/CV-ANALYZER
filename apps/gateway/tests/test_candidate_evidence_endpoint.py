"""Story 6.2: the authorized full-Evidence projection
(`GET /workspace/candidates/{candidate_id}/evidence`) — every Matched/
Partial/Needs Validation/Not Found row with locator/excerpt, never the
capped strengths/gaps summary `candidate_report` already renders. Mirrors
test_candidate_report_endpoint.py's own-fixture/autouse-truncate live-
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
    CandidateJob,
    CandidateProposal,
    CandidateResult,
    Document,
    JobRequirement,
    ParseArtifact,
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
            "TRUNCATE TABLE sessions, users, shortlists, candidate_proposals, candidate_results, "
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
    source_units: list | None = None,
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
    if source_units is not None:
        db.add(
            ParseArtifact(
                id=str(uuid.uuid4()),
                candidate_id=candidate_id,
                document_id=document_id,
                document_content_version=1,
                parser_pipeline_version="v1",
                source_units_json=source_units,
                blocks_json=[],
                gate_codes=[],
                coherent_block_count=5,
                created_at=now,
            )
        )
    db.commit()
    return candidate_id


def test_full_state_mix_returns_every_row_with_locator_and_excerpt(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    matched_req = _seed_requirement(db, session_id, "R1", "mandatory_skills", "Kubernetes operations")
    partial_req = _seed_requirement(db, session_id, "R2", "relevant_experience", "Incident leadership")
    needs_val_req = _seed_requirement(db, session_id, "R3", "domain_fit", "Fintech domain experience")
    not_found_req = _seed_requirement(db, session_id, "R4", "preferred_skills_tools", "Terraform")
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))

    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        proposal_items=[
            {"job_requirement_id": matched_req, "state": "Matched", "locator": "u-pdf", "excerpt": "ran K8s clusters"},
            {"job_requirement_id": partial_req, "state": "Partial", "locator": "u-docx", "excerpt": "led incidents"},
            {"job_requirement_id": needs_val_req, "state": "Needs Validation", "locator": "", "excerpt": ""},
            {"job_requirement_id": not_found_req, "state": "Not Found", "locator": "", "excerpt": ""},
        ],
        source_units=[
            {
                "id": "u-pdf",
                "text": "ran K8s clusters",
                "locator": {"type": "pdf", "page": 2, "span": {"start": 0, "end": 10}, "excerpt": "ran K8s clusters"},
            },
            {
                "id": "u-docx",
                "text": "led incidents",
                "locator": {"type": "docx", "path": "body/p[3]", "span": {"start": 0, "end": 10}, "excerpt": "led incidents"},
            },
        ],
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/evidence")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["candidate_id"] == candidate_id
    assert len(body["rows"]) == 4
    by_state = {row["state"]: row for row in body["rows"]}
    assert by_state["Matched"]["locator_description"] == "Page 2"
    assert by_state["Matched"]["excerpt"] == "ran K8s clusters"
    assert by_state["Matched"]["recruiter_review"] == "NoReviewFlag"
    assert by_state["Partial"]["locator_description"] == "body/p[3]"
    assert by_state["Needs Validation"]["locator_description"] is None
    assert by_state["Not Found"]["locator_description"] is None
    assert by_state["Not Found"]["excerpt"] == ""


def test_needs_review_candidate_with_empty_items_returns_empty_rows(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2", outcome="NeedsReview",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/evidence")
    client.cookies.clear()

    assert response.status_code == 200
    assert response.json()["rows"] == []


def test_failed_candidate_has_no_evidence_view(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D3", outcome="Failed",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/evidence")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_missing_candidate_id_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{uuid.uuid4()}/evidence")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_cross_owner_candidate_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    owner_session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)
    revision_id = _seed_revision(db, owner_session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=owner_session_id, document_reference="D1", outcome="NewResult",
    )

    _, other_token = _admitted_identity_and_token(db)

    client.cookies.set("session", other_token)
    response = client.get(f"/workspace/candidates/{candidate_id}/evidence")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_unrecognized_component_on_one_requirement_is_skipped_not_a_500(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    good_req = _seed_requirement(db, session_id, "R1", "mandatory_skills", "Kubernetes operations")
    bad_req = _seed_requirement(db, session_id, "R2", "not_a_real_component", "Something else")
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        proposal_items=[
            {"job_requirement_id": good_req, "state": "Matched", "locator": "", "excerpt": ""},
            {"job_requirement_id": bad_req, "state": "Matched", "locator": "", "excerpt": ""},
        ],
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/evidence")
    client.cookies.clear()

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["requirement_text"] == "Kubernetes operations"
