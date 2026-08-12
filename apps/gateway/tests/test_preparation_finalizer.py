"""Story 3.5: the gateway preparation coordinator (`scan_and_finalize`) —
level-triggered claim/recheck/atomic-freeze, restart/duplicate-scan safety,
and real concurrency. Mirrors test_analyze_api.py's live-DATABASE_URL-skip
pattern and fixture conventions.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.adapters.models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateJob,
    Document,
    JobRequirement,
    RevisionMembership,
    ScoringConfiguration,
    StartPreparation,
)
from src.adapters.preparation_finalizer import scan_and_finalize

VALID_JOB_DESCRIPTION = "x" * 200

_engine = create_engine(os.environ["DATABASE_URL"])
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


@pytest.fixture()
def db():
    """Every read after `scan_and_finalize`'s own commit opens a new,
    implicit transaction (SQLAlchemy autobegin) — closing here is required,
    not cosmetic: a leftover "idle in transaction" session holds table
    locks that block unrelated fixtures (e.g. conftest's TRUNCATE) in
    later-running test files sharing this same live database."""
    session = _SessionFactory()
    yield session
    session.close()


def _seed_locked_session_with_document(db, *, job_description_version: int = 1) -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    db.add(
        AnalysisSession(
            id=session_id,
            creator_issuer="local",
            creator_subject=f"subject-{session_id}",
            status="preparing_to_start",
            created_at=datetime.now(timezone.utc),
            job_description_text=VALID_JOB_DESCRIPTION,
            job_description_version=job_description_version,
        )
    )
    db.add(
        Document(
            id=document_id,
            analysis_session_id=session_id,
            document_reference="DOC-001",
            original_filename="resume.pdf",
            content_version=1,
            storage_path="/tmp/x",
            size_bytes=10,
            content_type="application/pdf",
            status="ready",
            idempotency_key=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return session_id, document_id


def _seed_locked_session_without_documents(db, *, job_description_version: int = 1) -> str:
    session_id = str(uuid.uuid4())
    db.add(
        AnalysisSession(
            id=session_id,
            creator_issuer="local",
            creator_subject=f"subject-{session_id}",
            status="preparing_to_start",
            created_at=datetime.now(timezone.utc),
            job_description_text=VALID_JOB_DESCRIPTION,
            job_description_version=job_description_version,
        )
    )
    db.commit()
    return session_id


def _seed_validated_preparation(
    db,
    session_id: str,
    document_id: str,
    *,
    job_description_version: int = 1,
    proposal_items: list[dict] | None = None,
) -> str:
    if proposal_items is None:
        proposal_items = [
            {
                "component": "mandatory_skills",
                "classification": "mandatory",
                "text": "Python",
                "source_start": 0,
                "source_end": 6,
            }
        ]
    prep_id = str(uuid.uuid4())
    db.add(
        StartPreparation(
            id=prep_id,
            analysis_session_id=session_id,
            status="validated",
            job_description_version=job_description_version,
            document_versions={document_id: 1},
            idempotency_key=str(uuid.uuid4()),
            request_fingerprint="fp",
            created_at=datetime.now(timezone.utc),
            attempt=1,
            proposal_json={"items": proposal_items},
        )
    )
    db.commit()
    return prep_id


def test_valid_proposal_freezes_revision_and_membership(db):
    session_id, document_id = _seed_locked_session_with_document(db)
    prep_id = _seed_validated_preparation(db, session_id, document_id)

    claimed = scan_and_finalize(db)
    assert claimed is True

    session_row = db.execute(select(AnalysisSession).where(AnalysisSession.id == session_id)).scalar_one()
    assert session_row.status == "frozen_inputs"

    prep_row = db.execute(select(StartPreparation).where(StartPreparation.id == prep_id)).scalar_one()
    assert prep_row.status == "frozen"

    requirements = db.execute(
        select(JobRequirement).where(JobRequirement.analysis_session_id == session_id)
    ).scalars().all()
    assert len(requirements) == 1
    assert requirements[0].display_id == "JR-001"

    scoring = db.execute(
        select(ScoringConfiguration).where(ScoringConfiguration.analysis_session_id == session_id)
    ).scalars().all()
    assert sum(row.effective_weight_bps for row in scoring) == 10_000

    revisions = db.execute(
        select(AnalysisRevision).where(AnalysisRevision.analysis_session_id == session_id)
    ).scalars().all()
    assert len(revisions) == 1
    assert revisions[0].revision_number == 1

    candidates = db.execute(select(Candidate).where(Candidate.analysis_session_id == session_id)).scalars().all()
    assert len(candidates) == 1
    assert candidates[0].document_id == document_id

    memberships = db.execute(
        select(RevisionMembership).where(RevisionMembership.analysis_revision_id == revisions[0].id)
    ).scalars().all()
    assert len(memberships) == 1
    assert memberships[0].outcome == "queued"

    jobs = db.execute(
        select(CandidateJob).where(CandidateJob.analysis_revision_id == revisions[0].id)
    ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == "queued"


def test_zero_ready_documents_terminates_and_unlocks(db):
    """Unreachable via the normal Analyze flow (3.4 requires >=1 Ready
    Document before locking) but defended anyway (review finding) —
    directly seeds a validated preparation with an empty document snapshot
    to prove the guard fires rather than freezing a Candidate-less Revision."""
    session_id = _seed_locked_session_without_documents(db)
    prep_id = str(uuid.uuid4())
    db.add(
        StartPreparation(
            id=prep_id,
            analysis_session_id=session_id,
            status="validated",
            job_description_version=1,
            document_versions={},
            idempotency_key=str(uuid.uuid4()),
            request_fingerprint="fp",
            created_at=datetime.now(timezone.utc),
            attempt=1,
            proposal_json={
                "items": [
                    {
                        "component": "mandatory_skills",
                        "classification": "mandatory",
                        "text": "Python",
                        "source_start": 0,
                        "source_end": 6,
                    }
                ]
            },
        )
    )
    db.commit()

    claimed = scan_and_finalize(db)
    assert claimed is True

    session_row = db.execute(select(AnalysisSession).where(AnalysisSession.id == session_id)).scalar_one()
    assert session_row.status == "draft"

    prep_row = db.execute(select(StartPreparation).where(StartPreparation.id == prep_id)).scalar_one()
    assert prep_row.status == "failed"
    assert prep_row.failure_reason == "no_ready_documents"

    revisions = db.execute(
        select(AnalysisRevision).where(AnalysisRevision.analysis_session_id == session_id)
    ).scalars().all()
    assert revisions == []


def test_stale_job_description_version_terminates_and_unlocks(db):
    session_id, document_id = _seed_locked_session_with_document(db, job_description_version=2)
    prep_id = _seed_validated_preparation(db, session_id, document_id, job_description_version=1)

    claimed = scan_and_finalize(db)
    assert claimed is True

    session_row = db.execute(select(AnalysisSession).where(AnalysisSession.id == session_id)).scalar_one()
    assert session_row.status == "draft"

    prep_row = db.execute(select(StartPreparation).where(StartPreparation.id == prep_id)).scalar_one()
    assert prep_row.status == "failed"

    revisions = db.execute(
        select(AnalysisRevision).where(AnalysisRevision.analysis_session_id == session_id)
    ).scalars().all()
    assert revisions == []


def test_conflicting_duplicate_proposal_terminates_and_unlocks(db):
    session_id, document_id = _seed_locked_session_with_document(db)
    conflicting_items = [
        {"component": "mandatory_skills", "classification": "mandatory", "text": "SQL", "source_start": 0, "source_end": 3},
        {"component": "mandatory_skills", "classification": "preferred", "text": "sql", "source_start": 10, "source_end": 13},
    ]
    prep_id = _seed_validated_preparation(db, session_id, document_id, proposal_items=conflicting_items)

    claimed = scan_and_finalize(db)
    assert claimed is True

    session_row = db.execute(select(AnalysisSession).where(AnalysisSession.id == session_id)).scalar_one()
    assert session_row.status == "draft"

    prep_row = db.execute(select(StartPreparation).where(StartPreparation.id == prep_id)).scalar_one()
    assert prep_row.status == "failed"
    assert prep_row.failure_reason == "conflicting_requirements"


def test_restart_or_duplicate_scan_is_a_no_op_after_frozen(db):
    session_id, document_id = _seed_locked_session_with_document(db)
    _seed_validated_preparation(db, session_id, document_id)

    first = scan_and_finalize(db)
    assert first is True

    revisions_before = db.execute(
        select(AnalysisRevision).where(AnalysisRevision.analysis_session_id == session_id)
    ).scalars().all()

    second = scan_and_finalize(db)
    assert second is False

    revisions_after = db.execute(
        select(AnalysisRevision).where(AnalysisRevision.analysis_session_id == session_id)
    ).scalars().all()
    assert len(revisions_after) == len(revisions_before) == 1


def test_concurrent_scans_only_one_finalizes(db):
    session_id, document_id = _seed_locked_session_with_document(db)
    _seed_validated_preparation(db, session_id, document_id)

    def run_scan():
        worker_db = _SessionFactory()
        try:
            return scan_and_finalize(worker_db)
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_scan(), range(2)))

    assert sorted(results) == [False, True]

    revisions = db.execute(
        select(AnalysisRevision).where(AnalysisRevision.analysis_session_id == session_id)
    ).scalars().all()
    assert len(revisions) == 1
