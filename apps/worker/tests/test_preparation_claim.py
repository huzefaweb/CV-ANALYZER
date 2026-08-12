"""Real-PostgreSQL tests for the minimal-CAS start_preparations claim loop
(Story 3.5). Skipped (not failed) when WORKER_DATABASE_URL is unset, mirrors
test_job_lease_restart_recovery.py's pattern. Precondition: `docker compose
up -d postgres`, gateway migrations applied (creates start_preparations /
analysis_sessions).
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from src.adapters import preparation_claim

WORKER_DATABASE_URL = os.environ.get("WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not WORKER_DATABASE_URL,
    reason="WORKER_DATABASE_URL not set; requires a live PostgreSQL instance with gateway migrations applied",
)


@pytest.fixture()
def conn():
    connection = psycopg.connect(WORKER_DATABASE_URL, autocommit=True)
    # Isolate from leftover rows any other test/run may have left behind —
    # claim_queued's `ORDER BY created_at LIMIT 1` would otherwise pick up a
    # stale row instead of the one this test just seeded.
    connection.execute("TRUNCATE TABLE start_preparations, analysis_sessions RESTART IDENTITY CASCADE")
    yield connection
    connection.close()


def _seed_session_and_preparation(conn, *, attempt: int = 1) -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    prep_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO analysis_sessions
            (id, creator_issuer, creator_subject, status, created_at, job_description_text, job_description_version)
        VALUES (%s, 'local', %s, 'preparing_to_start', now(), 'A sufficiently long job description text.', 1)
        """,
        (session_id, f"subject-{session_id}"),
    )
    conn.execute(
        """
        INSERT INTO start_preparations
            (id, analysis_session_id, status, job_description_version, document_versions,
             idempotency_key, request_fingerprint, created_at, attempt)
        VALUES (%s, %s, 'queued', 1, '{}', 'idem-1', 'fp-1', now(), %s)
        """,
        (prep_id, session_id, attempt),
    )
    return session_id, prep_id


def test_claim_transitions_queued_to_deriving(conn):
    _, prep_id = _seed_session_and_preparation(conn)
    claimed = preparation_claim.claim_queued(conn)
    assert claimed is not None
    assert claimed.id == prep_id
    status = conn.execute("SELECT status FROM start_preparations WHERE id = %s", (prep_id,)).fetchone()[0]
    assert status == "deriving"


def test_concurrent_claim_never_double_claims(conn):
    _seed_session_and_preparation(conn)

    def try_claim():
        with psycopg.connect(WORKER_DATABASE_URL, autocommit=True) as c:
            return preparation_claim.claim_queued(c)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: try_claim(), range(2)))

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1


def test_stage_success_writes_proposal_and_validated_status(conn):
    _, prep_id = _seed_session_and_preparation(conn)
    preparation_claim.claim_queued(conn)
    ok = preparation_claim.stage_success(conn, prep_id, {"items": []})
    assert ok is True
    row = conn.execute(
        "SELECT status, proposal_json FROM start_preparations WHERE id = %s", (prep_id,)
    ).fetchone()
    assert row[0] == "validated"
    assert row[1] == {"items": []}


def test_stage_failure_with_attempt_1_requeues_with_attempt_2(conn):
    session_id, prep_id = _seed_session_and_preparation(conn, attempt=1)
    claimed = preparation_claim.claim_queued(conn)
    ok = preparation_claim.stage_failure(conn, prep_id, session_id, claimed.attempt, "timeout")
    assert ok is True
    row = conn.execute("SELECT status, attempt FROM start_preparations WHERE id = %s", (prep_id,)).fetchone()
    assert row[0] == "queued"
    assert row[1] == 2
    # Not terminal yet — session must stay locked (still retryable).
    session_status = conn.execute(
        "SELECT status FROM analysis_sessions WHERE id = %s", (session_id,)
    ).fetchone()[0]
    assert session_status == "preparing_to_start"


def test_stage_failure_with_attempt_2_terminates_failed_and_unlocks_session(conn):
    session_id, prep_id = _seed_session_and_preparation(conn, attempt=2)
    claimed = preparation_claim.claim_queued(conn)
    ok = preparation_claim.stage_failure(conn, prep_id, session_id, claimed.attempt, "timeout")
    assert ok is True
    row = conn.execute("SELECT status FROM start_preparations WHERE id = %s", (prep_id,)).fetchone()
    assert row[0] == "failed"
    # AC#2: inputs unlock once the preparation reaches a terminal state —
    # exhausting attempt 2 on the worker side is one such terminus.
    session_status = conn.execute(
        "SELECT status FROM analysis_sessions WHERE id = %s", (session_id,)
    ).fetchone()[0]
    assert session_status == "draft"


def test_fetch_job_description_text(conn):
    session_id, _ = _seed_session_and_preparation(conn)
    text = preparation_claim.fetch_job_description_text(conn, session_id)
    assert text == "A sufficiently long job description text."


def test_fetch_job_description_text_raises_for_dangling_session(conn):
    with pytest.raises(preparation_claim.DanglingPreparationError):
        preparation_claim.fetch_job_description_text(conn, str(uuid.uuid4()))
