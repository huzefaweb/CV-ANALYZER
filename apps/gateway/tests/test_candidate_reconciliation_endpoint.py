"""Story 6.2: the authorized score-reconciliation projection
(`GET /workspace/candidates/{candidate_id}/reconciliation`) — base/
effective weights, N/A redistribution, and the already-exact, already-
largest-remainder-reconciled `component_contribution_display` reshaped per
component. Mirrors test_candidate_report_endpoint.py's own-fixture/
autouse-truncate live-DATABASE_URL pattern.
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
    CandidateResult,
    Document,
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
            "candidate_identities, scoring_configurations, revision_memberships, candidate_jobs, "
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


def _seed_scoring_configuration(db, session_id: str, applicable_weights: dict[str, int]) -> None:
    """`applicable_weights` maps component -> effective_weight_bps for the
    applicable subset; every other frozen component gets an N/A row
    (applicable=False, effective_weight_bps=0) — matching how
    `preparation_finalizer.py` writes one row per component every session."""
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
    headline_whole_percent: int | None = None,
    precise_score_percent: Decimal | None = None,
    component_contribution_display: dict | None = None,
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
    result_fields: dict = dict(headline_whole_percent=headline_whole_percent)
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
    db.commit()
    return candidate_id


def test_full_reconciliation_contributions_sum_to_precise_score_percent(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 6000, "relevant_experience": 4000})
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        headline_whole_percent=70,
        precise_score_percent=Decimal("70.00"),
        component_contribution_display={"mandatory_skills": "45.00", "relevant_experience": "25.00"},
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/reconciliation")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["precise_score_percent"] == "70.00"
    assert body["headline_whole_percent"] == 70

    by_component = {c["component"]: c for c in body["components"]}
    assert len(by_component) == 7
    assert by_component["mandatory_skills"]["applicable"] is True
    assert by_component["mandatory_skills"]["base_weight_percent"] == "30.00"
    assert by_component["mandatory_skills"]["effective_weight_percent"] == "60.00"
    assert by_component["mandatory_skills"]["contribution_percent"] == "45.00"

    total_contribution = sum(
        Decimal(c["contribution_percent"]) for c in body["components"] if c["contribution_percent"] is not None
    )
    assert total_contribution == Decimal(body["precise_score_percent"])


def test_na_component_shows_no_effective_weight_or_contribution(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db,
        revision_id=revision_id,
        session_id=session_id,
        document_reference="D1",
        outcome="NewResult",
        headline_whole_percent=90,
        precise_score_percent=Decimal("90.00"),
        component_contribution_display={"mandatory_skills": "90.00"},
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/reconciliation")
    client.cookies.clear()

    by_component = {c["component"]: c for c in response.json()["components"]}
    assert by_component["domain_fit"]["applicable"] is False
    assert by_component["domain_fit"]["effective_weight_percent"] is None
    assert by_component["domain_fit"]["contribution_percent"] is None
    assert by_component["domain_fit"]["base_weight_percent"] == "5.00"

    applicable_effective_total = sum(
        Decimal(c["effective_weight_percent"]) for c in response.json()["components"] if c["effective_weight_percent"] is not None
    )
    assert applicable_effective_total == Decimal("100.00")


def test_needs_review_candidate_has_no_reconciliation(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D2", outcome="NeedsReview",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/reconciliation")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_failed_candidate_has_no_reconciliation(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D3", outcome="Failed",
    )

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{candidate_id}/reconciliation")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_missing_candidate_id_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    _seed_session(db, identity.issuer, identity.subject)

    client.cookies.set("session", token)
    response = client.get(f"/workspace/candidates/{uuid.uuid4()}/reconciliation")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_cross_owner_candidate_is_the_same_neutral_404(db):
    owner_identity, _ = _admitted_identity_and_token(db)
    owner_session_id = _seed_session(db, owner_identity.issuer, owner_identity.subject)
    _seed_scoring_configuration(db, owner_session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, owner_session_id, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=owner_session_id, document_reference="D1",
        outcome="NewResult", headline_whole_percent=70, precise_score_percent=Decimal("70.00"),
        component_contribution_display={"mandatory_skills": "70.00"},
    )

    _, other_token = _admitted_identity_and_token(db)

    client.cookies.set("session", other_token)
    response = client.get(f"/workspace/candidates/{candidate_id}/reconciliation")
    client.cookies.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_stale_or_nonexistent_revision_number_is_the_same_neutral_404(db):
    identity, token = _admitted_identity_and_token(db)
    session_id = _seed_session(db, identity.issuer, identity.subject)
    _seed_scoring_configuration(db, session_id, {"mandatory_skills": 10000})
    revision_id = _seed_revision(db, session_id, revision_number=1, published_at=datetime.now(timezone.utc))
    candidate_id = _seed_candidate(
        db, revision_id=revision_id, session_id=session_id, document_reference="D1",
        outcome="NewResult", headline_whole_percent=70, precise_score_percent=Decimal("70.00"),
        component_contribution_display={"mandatory_skills": "70.00"},
    )
    _seed_revision(db, session_id, revision_number=2, published_at=None)  # still processing

    client.cookies.set("session", token)
    unpublished = client.get(f"/workspace/candidates/{candidate_id}/reconciliation", params={"revision_number": 2})
    nonexistent = client.get(f"/workspace/candidates/{candidate_id}/reconciliation", params={"revision_number": 999})
    client.cookies.clear()

    assert unpublished.status_code == 404
    assert nonexistent.status_code == 404
