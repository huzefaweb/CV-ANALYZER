"""Story 7.2: the gateway Question coordinator (`scan_and_finalize_questions`)
— publish on a valid completed proposal, reject an incomplete one back to
'failed', pass through an already-worker-exhausted 'failed' job untouched,
and unstick orphaned/stale-membership claims without publishing. Mirrors
test_candidate_finalizer.py's live-DATABASE_URL skip pattern and fixture
conventions.
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

from src.adapters.question_finalizer import scan_and_finalize_questions
from src.adapters.models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    QuestionSetJob,
    QuestionSetProposal,
    RevisionMembership,
)

VALID_JOB_DESCRIPTION = "x" * 200

_engine = create_engine(os.environ["DATABASE_URL"])
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

_CATEGORIES = [
    "technical_functional",
    "experience_verification",
    "gap_focused",
    "behavioral",
    "follow_up",
]


def _valid_items() -> list[dict]:
    return [
        {"number": i + 1, "category": _CATEGORIES[i % 5], "text": f"Question {i + 1}?", "source_requirement_id": ""}
        for i in range(10)
    ]


@pytest.fixture()
def db():
    session = _SessionFactory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db):
    # This coordinator's own claim query is an unscoped `WHERE status IN
    # (...)` scan across the whole question_set_jobs table — truncate before
    # every test so the scan only ever sees this module's own rows.
    db.execute(
        text(
            "TRUNCATE TABLE question_set_versions, question_set_proposals, question_set_jobs, "
            "revision_memberships, candidates, analysis_revisions, analysis_sessions CASCADE"
        )
    )
    db.commit()
    yield


def _seed_chain(db, *, job_status: str, failure_reason: str | None = None):
    now = datetime.now(timezone.utc)
    session_id = str(uuid.uuid4())
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
            outcome="NewResult",
            created_at=now,
        )
    )
    db.add(
        QuestionSetJob(
            id=job_id,
            candidate_id=candidate_id,
            analysis_revision_id=revision_id,
            status=job_status,
            idempotency_key="key-1",
            created_at=now,
            attempt=2,
            failure_reason=failure_reason,
        )
    )
    db.commit()
    return dict(session_id=session_id, candidate_id=candidate_id, revision_id=revision_id, job_id=job_id)


def _add_proposal(db, ids, *, items: list[dict]):
    db.add(
        QuestionSetProposal(
            id=str(uuid.uuid4()),
            question_set_job_id=ids["job_id"],
            candidate_id=ids["candidate_id"],
            analysis_revision_id=ids["revision_id"],
            items_json=items,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def test_valid_completed_proposal_publishes_and_marks_published(db):
    ids = _seed_chain(db, job_status="completed")
    _add_proposal(db, ids, items=_valid_items())

    assert scan_and_finalize_questions(db) is True

    job_status = db.execute(text("SELECT status FROM question_set_jobs WHERE id = :id"), {"id": ids["job_id"]}).scalar_one()
    assert job_status == "published"

    version = db.execute(
        text("SELECT version, items_json FROM question_set_versions WHERE question_set_job_id = :id"),
        {"id": ids["job_id"]},
    ).mappings().one()
    assert version["version"] == 1
    assert len(version["items_json"]) == 10


def test_publish_does_not_mutate_result_rank_review_or_shortlist(db):
    ids = _seed_chain(db, job_status="completed")
    _add_proposal(db, ids, items=_valid_items())

    membership_before = db.execute(
        text("SELECT outcome FROM revision_memberships WHERE candidate_id = :cid"), {"cid": ids["candidate_id"]}
    ).scalar_one()

    assert scan_and_finalize_questions(db) is True

    membership_after = db.execute(
        text("SELECT outcome FROM revision_memberships WHERE candidate_id = :cid"), {"cid": ids["candidate_id"]}
    ).scalar_one()
    assert membership_after == membership_before

    result_count = db.execute(
        text("SELECT count(*) FROM candidate_results WHERE candidate_id = :cid"), {"cid": ids["candidate_id"]}
    ).scalar_one()
    assert result_count == 0

    evidence_review_count = db.execute(
        text("SELECT count(*) FROM evidence_reviews WHERE candidate_id = :cid"), {"cid": ids["candidate_id"]}
    ).scalar_one()
    assert evidence_review_count == 0

    shortlist_count = db.execute(
        text("SELECT count(*) FROM shortlists WHERE candidate_id = :cid"), {"cid": ids["candidate_id"]}
    ).scalar_one()
    assert shortlist_count == 0


def test_incomplete_proposal_rejects_to_failed_and_publishes_nothing(db):
    ids = _seed_chain(db, job_status="completed")
    incomplete = _valid_items()[:9]  # wrong count -> validate_complete_set rejects
    _add_proposal(db, ids, items=incomplete)

    assert scan_and_finalize_questions(db) is True

    job = db.execute(
        text("SELECT status, failure_reason FROM question_set_jobs WHERE id = :id"), {"id": ids["job_id"]}
    ).mappings().one()
    assert job["status"] == "failed"
    assert job["failure_reason"] == "incomplete_proposal"

    count = db.execute(
        text("SELECT count(*) FROM question_set_versions WHERE question_set_job_id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert count == 0


def test_malformed_proposal_shape_rejects_to_failed_instead_of_crashing(db):
    """Review fix (Blind Hunter/Edge Case Hunter, convergent High): a
    malformed items_json (missing 'number' key) must not escape as an
    uncaught KeyError and permanently head-of-line-block the claim queue —
    it must reject to 'failed' exactly like a structurally-invalid-but-
    well-typed proposal does."""
    ids = _seed_chain(db, job_status="completed")
    malformed = [{"category": "gap_focused", "text": "no number key"}] * 10
    _add_proposal(db, ids, items=malformed)

    assert scan_and_finalize_questions(db) is True

    job = db.execute(
        text("SELECT status, failure_reason FROM question_set_jobs WHERE id = :id"), {"id": ids["job_id"]}
    ).mappings().one()
    assert job["status"] == "failed"
    assert job["failure_reason"] == "incomplete_proposal"

    count = db.execute(
        text("SELECT count(*) FROM question_set_versions WHERE question_set_job_id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert count == 0

    # The row is now 'failed' — a terminal, claimable-but-inert status the
    # coordinator leaves alone (matches test_worker_exhausted_failed_job_is_
    # claimed_but_untouched's identical shape). Re-scanning must not
    # re-crash on it and must not publish a version.
    assert scan_and_finalize_questions(db) is True
    count_after_rescan = db.execute(
        text("SELECT count(*) FROM question_set_versions WHERE question_set_job_id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert count_after_rescan == 0


def test_worker_exhausted_failed_job_is_claimed_but_untouched(db):
    ids = _seed_chain(db, job_status="failed", failure_reason="provider_unavailable")

    assert scan_and_finalize_questions(db) is True

    job = db.execute(
        text("SELECT status, failure_reason FROM question_set_jobs WHERE id = :id"), {"id": ids["job_id"]}
    ).mappings().one()
    assert job["status"] == "failed"
    assert job["failure_reason"] == "provider_unavailable"


def test_duplicate_scan_after_publish_is_idempotent(db):
    ids = _seed_chain(db, job_status="completed")
    _add_proposal(db, ids, items=_valid_items())

    assert scan_and_finalize_questions(db) is True
    # question_set_jobs.status is now 'published' — outside the WHERE clause.
    assert scan_and_finalize_questions(db) is False

    count = db.execute(
        text("SELECT count(*) FROM question_set_versions WHERE question_set_job_id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert count == 1


def test_stale_membership_unsticks_job_without_publishing(db):
    ids = _seed_chain(db, job_status="completed")
    _add_proposal(db, ids, items=_valid_items())
    db.execute(
        text("UPDATE revision_memberships SET outcome = 'NeedsReview' WHERE candidate_id = :cid"),
        {"cid": ids["candidate_id"]},
    )
    db.commit()

    assert scan_and_finalize_questions(db) is True

    job = db.execute(
        text("SELECT status, failure_reason FROM question_set_jobs WHERE id = :id"), {"id": ids["job_id"]}
    ).mappings().one()
    assert job["status"] == "unrecoverable"
    assert job["failure_reason"] == "unstuck"

    count = db.execute(
        text("SELECT count(*) FROM question_set_versions WHERE question_set_job_id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert count == 0

    # A second scan must not reclaim the now-unstuck row again.
    assert scan_and_finalize_questions(db) is False


def test_orphaned_candidate_unsticks_without_starving_the_queue(db):
    ids = _seed_chain(db, job_status="completed")
    _add_proposal(db, ids, items=_valid_items())
    db.execute(text("DELETE FROM candidates WHERE id = :id"), {"id": ids["candidate_id"]})
    db.commit()

    healthy = _seed_chain(db, job_status="completed")
    _add_proposal(db, healthy, items=_valid_items())

    assert scan_and_finalize_questions(db) is True  # unsticks the orphaned row
    orphaned_status = db.execute(
        text("SELECT status FROM question_set_jobs WHERE id = :id"), {"id": ids["job_id"]}
    ).scalar_one()
    assert orphaned_status == "unrecoverable"

    assert scan_and_finalize_questions(db) is True  # now claims the healthy row
    healthy_status = db.execute(
        text("SELECT status FROM question_set_jobs WHERE id = :id"), {"id": healthy["job_id"]}
    ).scalar_one()
    assert healthy_status == "published"
