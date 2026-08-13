"""Story 4.1: the gateway recovery-sweep coordinator (`sweep_stale`) —
reclaim-once, attempt-2-requeue, and terminal-exhaustion behavior across
both lease-bearing tables, plus real concurrency. Mirrors
test_preparation_finalizer.py's live-DATABASE_URL-skip pattern and fixture
conventions.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.adapters.models import AnalysisSession, CandidateJob, StartPreparation
from src.adapters.recovery_sweep import sweep_stale

VALID_JOB_DESCRIPTION = "x" * 200

_engine = create_engine(os.environ["DATABASE_URL"])
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


@pytest.fixture()
def db():
    session = _SessionFactory()
    yield session
    session.close()


def _seed_expired_preparation(db, *, attempt: int = 1, reclaim_count: int = 0) -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    prep_id = str(uuid.uuid4())
    db.add(
        AnalysisSession(
            id=session_id,
            creator_issuer="local",
            creator_subject=f"subject-{session_id}",
            status="preparing_to_start",
            created_at=datetime.now(timezone.utc),
            job_description_text=VALID_JOB_DESCRIPTION,
            job_description_version=1,
        )
    )
    db.add(
        StartPreparation(
            id=prep_id,
            analysis_session_id=session_id,
            status="deriving",
            job_description_version=1,
            document_versions={},
            idempotency_key=f"idem-{prep_id}",
            request_fingerprint="fp-1",
            created_at=datetime.now(timezone.utc),
            attempt=attempt,
            generation=1,
            reclaim_count=reclaim_count,
        )
    )
    db.commit()
    db.execute(
        text(
            "UPDATE start_preparations SET lease_token = 'stale-token', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": prep_id},
    )
    db.commit()
    return session_id, prep_id


def _seed_expired_candidate_job(db, *, attempt: int = 1, reclaim_count: int = 0) -> str:
    job_id = str(uuid.uuid4())
    db.add(
        CandidateJob(
            id=job_id,
            analysis_revision_id=str(uuid.uuid4()),
            candidate_id=str(uuid.uuid4()),
            status="claimed",
            created_at=datetime.now(timezone.utc),
            attempt=attempt,
            generation=1,
            reclaim_count=reclaim_count,
        )
    )
    db.commit()
    db.execute(
        text(
            "UPDATE candidate_jobs SET lease_token = 'stale-token', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": job_id},
    )
    db.commit()
    return job_id


def test_preparation_reclaimed_once_then_requeued_attempt_2_then_terminates_and_unlocks(db):
    session_id, prep_id = _seed_expired_preparation(db, attempt=1, reclaim_count=0)

    sweep_stale(db)
    row = db.execute(
        text("SELECT status, generation, reclaim_count FROM start_preparations WHERE id = :id"), {"id": prep_id}
    ).fetchone()
    assert row.status == "queued"
    assert row.generation == 2
    assert row.reclaim_count == 1

    # Re-expire (simulating a second crash) to exercise attempt-2 requeue.
    db.execute(
        text(
            "UPDATE start_preparations SET status = 'deriving', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": prep_id},
    )
    db.commit()
    sweep_stale(db)
    row = db.execute(text("SELECT status, attempt, reclaim_count FROM start_preparations WHERE id = :id"), {"id": prep_id}).fetchone()
    assert row.status == "queued"
    assert row.attempt == 2
    assert row.reclaim_count == 0
    session_status = db.execute(
        text("SELECT status FROM analysis_sessions WHERE id = :id"), {"id": session_id}
    ).scalar_one()
    assert session_status == "preparing_to_start"

    # Attempt 2 gets its own one-reclaim budget (AR-15: "at most one
    # same-attempt reclaim") — a third expiry reclaims within attempt 2,
    # not yet terminal.
    db.execute(
        text(
            "UPDATE start_preparations SET status = 'deriving', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": prep_id},
    )
    db.commit()
    sweep_stale(db)
    row = db.execute(
        text("SELECT status, attempt, reclaim_count FROM start_preparations WHERE id = :id"), {"id": prep_id}
    ).fetchone()
    assert row.status == "queued"
    assert row.attempt == 2
    assert row.reclaim_count == 1

    # A second lease loss within attempt 2 exhausts it — terminal failure,
    # session unlocks.
    db.execute(
        text(
            "UPDATE start_preparations SET status = 'deriving', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": prep_id},
    )
    db.commit()
    sweep_stale(db)
    row = db.execute(text("SELECT status FROM start_preparations WHERE id = :id"), {"id": prep_id}).fetchone()
    assert row.status == "failed"
    session_status = db.execute(
        text("SELECT status FROM analysis_sessions WHERE id = :id"), {"id": session_id}
    ).scalar_one()
    assert session_status == "draft"


def test_candidate_job_reclaimed_once_then_requeued_attempt_2_then_terminates(db):
    job_id = _seed_expired_candidate_job(db, attempt=1, reclaim_count=0)

    sweep_stale(db)
    row = db.execute(
        text("SELECT status, generation, reclaim_count FROM candidate_jobs WHERE id = :id"), {"id": job_id}
    ).fetchone()
    assert row.status == "queued"
    assert row.generation == 2
    assert row.reclaim_count == 1

    db.execute(
        text(
            "UPDATE candidate_jobs SET status = 'claimed', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": job_id},
    )
    db.commit()
    sweep_stale(db)
    row = db.execute(text("SELECT status, attempt, reclaim_count FROM candidate_jobs WHERE id = :id"), {"id": job_id}).fetchone()
    assert row.status == "queued"
    assert row.attempt == 2
    assert row.reclaim_count == 0

    db.execute(
        text(
            "UPDATE candidate_jobs SET status = 'claimed', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": job_id},
    )
    db.commit()
    sweep_stale(db)
    row = db.execute(
        text("SELECT status, attempt, reclaim_count FROM candidate_jobs WHERE id = :id"), {"id": job_id}
    ).fetchone()
    assert row.status == "queued"
    assert row.attempt == 2
    assert row.reclaim_count == 1

    db.execute(
        text(
            "UPDATE candidate_jobs SET status = 'claimed', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": job_id},
    )
    db.commit()
    sweep_stale(db)
    row = db.execute(text("SELECT status FROM candidate_jobs WHERE id = :id"), {"id": job_id}).fetchone()
    assert row.status == "failed"


def test_candidate_job_parsed_status_reclaimed_to_queued(db):
    """Story 4.4: a `'parsed'` row (mid-attempt checkpoint, lease still
    held) whose lease expires — e.g. worker crash between the parse
    checkpoint and provider-phase staging — must be reclaimed the same way
    a `'claimed'` row is, not left permanently stuck (claim_queued only
    ever selects `'queued'`)."""
    job_id = str(uuid.uuid4())
    db.add(
        CandidateJob(
            id=job_id,
            analysis_revision_id=str(uuid.uuid4()),
            candidate_id=str(uuid.uuid4()),
            status="parsed",
            created_at=datetime.now(timezone.utc),
            attempt=1,
            generation=1,
            reclaim_count=0,
        )
    )
    db.commit()
    db.execute(
        text(
            "UPDATE candidate_jobs SET lease_token = 'stale-token', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": job_id},
    )
    db.commit()

    sweep_stale(db)
    row = db.execute(
        text("SELECT status, generation, reclaim_count, lease_token FROM candidate_jobs WHERE id = :id"),
        {"id": job_id},
    ).fetchone()
    assert row.status == "queued"
    assert row.generation == 2
    assert row.reclaim_count == 1
    assert row.lease_token is None


def test_row_with_null_lease_expires_at_is_reclaimed(db):
    """A row with status='deriving' but lease_expires_at IS NULL (e.g. a
    pre-migration row that predates lease columns existing) must not be
    invisible to the sweep — `lease_expires_at <= now()` alone would never
    match NULL."""
    session_id = str(uuid.uuid4())
    prep_id = str(uuid.uuid4())
    db.add(
        AnalysisSession(
            id=session_id,
            creator_issuer="local",
            creator_subject=f"subject-{session_id}",
            status="preparing_to_start",
            created_at=datetime.now(timezone.utc),
            job_description_text=VALID_JOB_DESCRIPTION,
            job_description_version=1,
        )
    )
    db.add(
        StartPreparation(
            id=prep_id,
            analysis_session_id=session_id,
            status="deriving",
            job_description_version=1,
            document_versions={},
            idempotency_key=f"idem-{prep_id}",
            request_fingerprint="fp-1",
            created_at=datetime.now(timezone.utc),
            attempt=1,
            generation=0,
        )
    )
    db.commit()
    # generation=0, lease_token=None, lease_expires_at=None — never claimed
    # through claim_queued, simulating a row from before this story's
    # lease columns existed.

    sweep_stale(db)
    row = db.execute(text("SELECT status, generation FROM start_preparations WHERE id = :id"), {"id": prep_id}).fetchone()
    assert row.status == "queued"
    assert row.generation == 1


def test_concurrent_sweep_reclaims_exactly_once(db):
    _, prep_id = _seed_expired_preparation(db)

    def run_sweep():
        session = _SessionFactory()
        try:
            return sweep_stale(session)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run_sweep(), range(2)))

    row = db.execute(text("SELECT generation, reclaim_count FROM start_preparations WHERE id = :id"), {"id": prep_id}).fetchone()
    # FOR UPDATE SKIP LOCKED means only one of the two concurrent sweeps
    # could have touched this row — generation/reclaim_count advance once.
    assert row.generation == 2
    assert row.reclaim_count == 1
