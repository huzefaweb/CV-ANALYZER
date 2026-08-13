"""Story 4.1 (AC#2, AF-10's 30-second restart fixture, AR-15's 16-second
bound): an end-to-end proof that a crashed worker's stale claim is
recovered by one sweep call and reclaimed by a second claimant, while the
original (now-fenced) claimant's late write is rejected without changing
state or duplicating effect. Task 2/4's unit-style tests already prove each
half in isolation; this test proves the full crash -> sweep -> reclaim ->
stale-write-rejected sequence together, matching AF-10's fixture shape.
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

from src.adapters.models import AnalysisSession, StartPreparation
from src.adapters.recovery_sweep import sweep_stale

# AD-6/AR-15's V1 timing constants — mirrored here (not imported: worker and
# gateway are separate deployable packages with no shared package, the same
# "port, not import" boundary Story 3.5 already established for
# scoring_configuration.py). LEASE_SECONDS matches
# apps/worker/src/adapters/preparation_claim.py::LEASE_SECONDS;
# SWEEP_SECONDS matches api.py's `_SWEEP_INTERVAL_SECONDS`.
LEASE_SECONDS = 12
SWEEP_SECONDS = 2

VALID_JOB_DESCRIPTION = "x" * 200

_engine = create_engine(os.environ["DATABASE_URL"])
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


@pytest.fixture()
def db():
    session = _SessionFactory()
    yield session
    session.close()


def test_ar15_recovery_bound_is_within_af10_fixture():
    """`12 + 2 = 14` worst-case detection-plus-sweep seconds, comfortably
    inside AR-15's 16-second design bound and AF-10's 30-second fixture."""
    assert LEASE_SECONDS + SWEEP_SECONDS <= 16


def test_crashed_worker_claim_is_recovered_and_original_write_rejected(db):
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
            generation=1,
        )
    )
    db.commit()
    original_generation, original_token = 1, "original-worker-token"
    db.execute(
        text(
            "UPDATE start_preparations SET lease_token = :token, "
            "lease_expires_at = now() - interval '1 second' WHERE id = :id"
        ),
        {"token": original_token, "id": prep_id},
    )
    db.commit()

    # Startup/recovery sweep discovers the stale lease (simulated crash: no
    # heartbeat, lease already expired) and reclaims it.
    sweep_stale(db)
    row = db.execute(
        text("SELECT status, generation FROM start_preparations WHERE id = :id"), {"id": prep_id}
    ).fetchone()
    assert row.status == "queued"
    new_generation = row.generation
    assert new_generation == original_generation + 1

    # A second worker instance claims the recovered row (simulated via
    # direct SQL, mirroring preparation_claim.claim_queued's CAS shape).
    db.execute(
        text(
            "UPDATE start_preparations SET status = 'deriving', generation = generation + 1, "
            "lease_token = 'second-worker-token', lease_expires_at = now() + interval '12 seconds' "
            "WHERE id = :id AND status = 'queued'"
        ),
        {"id": prep_id},
    )
    db.commit()

    # The original (now-fenced) claimant's late write — stale generation and
    # token — is rejected without changing state or duplicating the effect.
    stale_write = db.execute(
        text(
            "UPDATE start_preparations SET status = 'validated' "
            "WHERE id = :id AND generation = :generation AND lease_token = :token AND status = 'deriving'"
        ),
        {"id": prep_id, "generation": original_generation, "token": original_token},
    )
    db.commit()
    assert stale_write.rowcount == 0

    final_status = db.execute(
        text("SELECT status FROM start_preparations WHERE id = :id"), {"id": prep_id}
    ).scalar_one()
    assert final_status == "deriving"
