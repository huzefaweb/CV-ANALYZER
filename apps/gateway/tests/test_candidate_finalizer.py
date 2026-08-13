"""Story 4.6: the gateway Candidate finalizer coordinator
(`scan_and_finalize_candidates`) — success/Needs Review/Failed
finalization, Shortlist default-insert without overwrite, publication
`requested_version` bump, and idempotency against duplicates/stale
membership/restarts. Mirrors test_recovery_sweep.py's live-DATABASE_URL
skip pattern and fixture conventions.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.adapters.candidate_finalizer import scan_and_finalize_candidates
from src.adapters.models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateJob,
    CandidateProposal,
    JobRequirement,
    ParseArtifact,
    RevisionMembership,
    ScoringConfiguration,
    Shortlist,
)

VALID_JOB_DESCRIPTION = "x" * 200

_engine = create_engine(os.environ["DATABASE_URL"])
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


@pytest.fixture()
def db():
    session = _SessionFactory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db):
    # This coordinator's own claim query is an unscoped `WHERE status IN
    # (...)` scan across the whole candidate_jobs table — unlike most other
    # coordinator tests (e.g. test_preparation_finalizer.py's `status =
    # 'validated'`), 'completed'/'failed' are shared, heavily-used status
    # values across many other test files' fixtures. Truncate before every
    # test in this module so the scan only ever sees this module's own rows.
    db.execute(
        text(
            "TRUNCATE TABLE candidate_results, shortlists, revision_memberships, candidate_jobs, "
            "candidate_proposals, parse_artifacts, candidates, analysis_revisions, "
            "job_requirements, scoring_configurations, analysis_sessions CASCADE"
        )
    )
    db.commit()
    yield


def _seed_chain(db, *, job_status: str, failure_reason: str | None = None):
    """One session, one mandatory_skills-only requirement (weight 10000
    bps), one Candidate, one Revision 1, one queued membership, one
    candidate_job at the given status."""
    now = datetime.now(timezone.utc)
    session_id = str(uuid.uuid4())
    requirement_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    revision_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    db.add(
        AnalysisSession(
            id=session_id,
            creator_issuer="local",
            creator_subject=f"subject-{session_id}",
            status="frozen_inputs",
            created_at=now,
            job_description_text=VALID_JOB_DESCRIPTION,
            job_description_version=1,
        )
    )
    db.add(
        JobRequirement(
            id=requirement_id,
            analysis_session_id=session_id,
            display_id="MS-1",
            component="mandatory_skills",
            classification="mandatory",
            canonical_text="Python",
            source_locators=[],
            created_at=now,
        )
    )
    db.add(
        ScoringConfiguration(
            id=str(uuid.uuid4()),
            analysis_session_id=session_id,
            component="mandatory_skills",
            applicable=True,
            effective_weight_bps=10000,
            created_at=now,
        )
    )
    db.add(
        Candidate(
            id=candidate_id,
            analysis_session_id=session_id,
            document_id=str(uuid.uuid4()),
            document_reference="R-1",
            created_at=now,
        )
    )
    db.add(
        AnalysisRevision(
            id=revision_id,
            analysis_session_id=session_id,
            revision_number=1,
            status="frozen",
            created_at=now,
            requested_version=0,
        )
    )
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            outcome="queued",
            created_at=now,
        )
    )
    db.add(
        CandidateJob(
            id=job_id,
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            status=job_status,
            created_at=now,
            attempt=2,
            failure_reason=failure_reason,
        )
    )
    db.commit()
    return dict(
        session_id=session_id,
        requirement_id=requirement_id,
        candidate_id=candidate_id,
        revision_id=revision_id,
        job_id=job_id,
    )


def _add_parse_artifact(db, ids, *, gate_codes: list[str]):
    db.add(
        ParseArtifact(
            id=str(uuid.uuid4()),
            candidate_id=ids["candidate_id"],
            document_id=str(uuid.uuid4()),
            document_content_version=1,
            parser_pipeline_version="v1",
            source_units_json=[],
            blocks_json=[],
            gate_codes=gate_codes,
            coherent_block_count=5,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _add_proposal(db, ids, *, items: list[dict], gate_codes: list[str] | None = None):
    db.add(
        CandidateProposal(
            id=str(uuid.uuid4()),
            candidate_job_id=ids["job_id"],
            candidate_id=ids["candidate_id"],
            analysis_revision_id=ids["revision_id"],
            items_json=items,
            gate_codes=gate_codes or [],
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _fetch_result(db, revision_id: str, candidate_id: str):
    return db.execute(
        text(
            "SELECT * FROM candidate_results WHERE analysis_revision_id = :rid AND candidate_id = :cid"
        ),
        {"rid": revision_id, "cid": candidate_id},
    ).mappings().one_or_none()


def test_success_finalizes_new_result_and_defaults_shortlist(db):
    ids = _seed_chain(db, job_status="completed")
    _add_parse_artifact(db, ids, gate_codes=[])
    _add_proposal(db, ids, items=[{"job_requirement_id": ids["requirement_id"], "state": "Matched"}])

    claimed = scan_and_finalize_candidates(db)
    assert claimed is True

    result = _fetch_result(db, ids["revision_id"], ids["candidate_id"])
    assert result["outcome"] == "NewResult"
    assert result["overall_score_bps_numerator"] is not None
    assert result["precise_score_percent"] == 100

    membership_outcome = db.execute(
        text("SELECT outcome FROM revision_memberships WHERE analysis_revision_id = :rid AND candidate_id = :cid"),
        {"rid": ids["revision_id"], "cid": ids["candidate_id"]},
    ).scalar_one()
    assert membership_outcome == "NewResult"

    job_status = db.execute(text("SELECT status FROM candidate_jobs WHERE id = :id"), {"id": ids["job_id"]}).scalar_one()
    assert job_status == "finalized"

    shortlist = db.execute(
        text("SELECT state, version FROM shortlists WHERE candidate_id = :cid"), {"cid": ids["candidate_id"]}
    ).fetchone()
    assert shortlist.state == "NotShortlisted"
    assert shortlist.version == 1

    requested_version = db.execute(
        text("SELECT requested_version FROM analysis_revisions WHERE id = :id"), {"id": ids["revision_id"]}
    ).scalar_one()
    assert requested_version == 1


def test_readable_content_gate_finalizes_needs_review(db):
    ids = _seed_chain(db, job_status="completed")
    _add_parse_artifact(db, ids, gate_codes=["TEXT_BELOW_500"])
    _add_proposal(db, ids, items=[{"job_requirement_id": ids["requirement_id"], "state": "Matched"}])

    claimed = scan_and_finalize_candidates(db)
    assert claimed is True

    result = _fetch_result(db, ids["revision_id"], ids["candidate_id"])
    assert result["outcome"] == "NeedsReview"
    assert result["gate_codes"] == ["TEXT_BELOW_500"]
    assert result["overall_score_bps_numerator"] is None

    membership_outcome = db.execute(
        text("SELECT outcome FROM revision_memberships WHERE analysis_revision_id = :rid AND candidate_id = :cid"),
        {"rid": ids["revision_id"], "cid": ids["candidate_id"]},
    ).scalar_one()
    assert membership_outcome == "NeedsReview"


def test_retryable_queued_job_is_never_claimed(db):
    """AC#2: a job requeued for attempt 2 sits at status='queued' — outside
    this coordinator's WHERE clause entirely. Proven by construction, not
    by new suppression logic."""
    ids = _seed_chain(db, job_status="queued", failure_reason="Analysis timed out")

    claimed = scan_and_finalize_candidates(db)
    assert claimed is False

    job_status = db.execute(text("SELECT status FROM candidate_jobs WHERE id = :id"), {"id": ids["job_id"]}).scalar_one()
    assert job_status == "queued"
    membership_outcome = db.execute(
        text("SELECT outcome FROM revision_memberships WHERE analysis_revision_id = :rid AND candidate_id = :cid"),
        {"rid": ids["revision_id"], "cid": ids["candidate_id"]},
    ).scalar_one()
    assert membership_outcome == "queued"


def test_exhausted_provider_failure_finalizes_failed_with_frozen_category(db):
    ids = _seed_chain(db, job_status="failed", failure_reason="Analysis timed out")

    claimed = scan_and_finalize_candidates(db)
    assert claimed is True

    result = _fetch_result(db, ids["revision_id"], ids["candidate_id"])
    assert result["outcome"] == "Failed"
    assert result["failure_category"] == "Analysis timed out"
    assert result["failure_correlation_reference"] == ids["job_id"]

    membership_outcome = db.execute(
        text("SELECT outcome FROM revision_memberships WHERE analysis_revision_id = :rid AND candidate_id = :cid"),
        {"rid": ids["revision_id"], "cid": ids["candidate_id"]},
    ).scalar_one()
    assert membership_outcome == "Failed"


def test_lease_exhausted_failure_maps_to_document_processing_interrupted(db):
    ids = _seed_chain(db, job_status="failed", failure_reason="lease_exhausted")

    scan_and_finalize_candidates(db)

    result = _fetch_result(db, ids["revision_id"], ids["candidate_id"])
    assert result["failure_category"] == "Document processing interrupted"


def test_duplicate_scan_after_finalization_is_idempotent(db):
    ids = _seed_chain(db, job_status="completed")
    _add_parse_artifact(db, ids, gate_codes=[])
    _add_proposal(db, ids, items=[{"job_requirement_id": ids["requirement_id"], "state": "Matched"}])

    assert scan_and_finalize_candidates(db) is True
    # candidate_jobs.status is now 'finalized' — outside the WHERE clause.
    assert scan_and_finalize_candidates(db) is False

    count = db.execute(
        text("SELECT count(*) FROM candidate_results WHERE analysis_revision_id = :rid AND candidate_id = :cid"),
        {"rid": ids["revision_id"], "cid": ids["candidate_id"]},
    ).scalar_one()
    assert count == 1

    requested_version = db.execute(
        text("SELECT requested_version FROM analysis_revisions WHERE id = :id"), {"id": ids["revision_id"]}
    ).scalar_one()
    assert requested_version == 1


def test_stale_membership_guard_unsticks_job_without_a_second_result(db):
    """Simulates a membership already advanced out of band while
    candidate_jobs.status is still 'completed' (e.g. a hypothetical crash
    between two halves of a prior run, or external tooling). The
    coordinator still claims the row (claim is keyed on candidate_jobs.status)
    and must not create a second candidate_results row or bump
    requested_version again — but it also must not leave the job stuck at
    'completed' forever, since that would make this row the permanent head
    of the claim queue and starve every legitimately-completed job behind
    it (review finding). It self-heals by moving the job straight to
    'finalized' with no matching result row."""
    ids = _seed_chain(db, job_status="completed")
    _add_parse_artifact(db, ids, gate_codes=[])
    _add_proposal(db, ids, items=[{"job_requirement_id": ids["requirement_id"], "state": "Matched"}])

    db.execute(
        text(
            "UPDATE revision_memberships SET outcome = 'NewResult' "
            "WHERE analysis_revision_id = :rid AND candidate_id = :cid"
        ),
        {"rid": ids["revision_id"], "cid": ids["candidate_id"]},
    )
    db.commit()

    claimed = scan_and_finalize_candidates(db)
    assert claimed is True

    count = db.execute(
        text("SELECT count(*) FROM candidate_results WHERE analysis_revision_id = :rid AND candidate_id = :cid"),
        {"rid": ids["revision_id"], "cid": ids["candidate_id"]},
    ).scalar_one()
    assert count == 0

    requested_version = db.execute(
        text("SELECT requested_version FROM analysis_revisions WHERE id = :id"), {"id": ids["revision_id"]}
    ).scalar_one()
    assert requested_version == 0

    job_status = db.execute(text("SELECT status FROM candidate_jobs WHERE id = :id"), {"id": ids["job_id"]}).scalar_one()
    assert job_status == "finalized"

    # A second scan must not reclaim the now-unstuck row again.
    assert scan_and_finalize_candidates(db) is False


def test_orphaned_candidate_row_unsticks_job_without_starving_the_queue(db):
    """A candidate_jobs row whose candidate_id has no matching candidates
    row (a data-integrity impossibility in practice) must not become a
    permanent head-of-queue blocker. A second, healthy job behind it must
    still be claimable on the very next scan."""
    ids = _seed_chain(db, job_status="completed")
    _add_parse_artifact(db, ids, gate_codes=[])
    _add_proposal(db, ids, items=[{"job_requirement_id": ids["requirement_id"], "state": "Matched"}])
    db.execute(text("DELETE FROM candidates WHERE id = :id"), {"id": ids["candidate_id"]})
    db.commit()

    # A second, healthy chain queued behind the orphaned one.
    healthy = _seed_chain(db, job_status="completed")
    _add_parse_artifact(db, healthy, gate_codes=[])
    _add_proposal(db, healthy, items=[{"job_requirement_id": healthy["requirement_id"], "state": "Matched"}])

    assert scan_and_finalize_candidates(db) is True  # unsticks the orphaned row
    orphaned_status = db.execute(
        text("SELECT status FROM candidate_jobs WHERE id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert orphaned_status == "finalized"
    orphaned_result_count = db.execute(
        text("SELECT count(*) FROM candidate_results WHERE candidate_job_id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert orphaned_result_count == 0

    assert scan_and_finalize_candidates(db) is True  # now claims the healthy row
    healthy_result = _fetch_result(db, healthy["revision_id"], healthy["candidate_id"])
    assert healthy_result["outcome"] == "NewResult"


def test_existing_shortlist_is_preserved_not_overwritten(db):
    ids = _seed_chain(db, job_status="completed")
    _add_parse_artifact(db, ids, gate_codes=[])
    _add_proposal(db, ids, items=[{"job_requirement_id": ids["requirement_id"], "state": "Matched"}])

    db.add(
        Shortlist(
            id=str(uuid.uuid4()),
            candidate_id=ids["candidate_id"],
            state="Shortlisted",
            version=3,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    scan_and_finalize_candidates(db)

    shortlist = db.execute(
        text("SELECT state, version FROM shortlists WHERE candidate_id = :cid"), {"cid": ids["candidate_id"]}
    ).fetchone()
    assert shortlist.state == "Shortlisted"
    assert shortlist.version == 3
