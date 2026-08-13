"""Real-PostgreSQL tests for the AD-6 lease/fencing claim mechanics on
`candidate_jobs` (Story 4.1). Proves table-symmetry with
`preparation_claim.py`'s equivalent behavior against the actual production
table — no FK constraints exist in this codebase (application-level joins
only), so `analysis_revision_id`/`candidate_id` are seeded as bare UUIDs,
matching every other adapter test's convention here. Skipped (not failed)
when WORKER_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from src.adapters import candidate_claim

WORKER_DATABASE_URL = os.environ.get("WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not WORKER_DATABASE_URL,
    reason="WORKER_DATABASE_URL not set; requires a live PostgreSQL instance with gateway migrations applied",
)


@pytest.fixture()
def conn():
    connection = psycopg.connect(WORKER_DATABASE_URL, autocommit=True)
    connection.execute("TRUNCATE TABLE candidate_jobs RESTART IDENTITY CASCADE")
    yield connection
    connection.close()


def _seed_candidate_job(conn, *, attempt: int = 1) -> str:
    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO candidate_jobs (id, analysis_revision_id, candidate_id, status, created_at, attempt)
        VALUES (%s, %s, %s, 'queued', now(), %s)
        """,
        (job_id, str(uuid.uuid4()), str(uuid.uuid4()), attempt),
    )
    return job_id


def test_claim_transitions_queued_to_claimed_with_fresh_lease(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.generation == 1
    assert len(claimed.token) == 32
    row = conn.execute(
        "SELECT status, generation, lease_token, lease_expires_at > now() FROM candidate_jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row[0] == "claimed"
    assert row[1] == 1
    assert row[2] == claimed.token
    assert row[3] is True


def test_concurrent_claim_never_double_claims(conn):
    _seed_candidate_job(conn)

    def try_claim():
        with psycopg.connect(WORKER_DATABASE_URL, autocommit=True) as c:
            return candidate_claim.claim_queued(c)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: try_claim(), range(2)))

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1


def test_heartbeat_extends_lease(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    before = conn.execute("SELECT lease_expires_at FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    conn.execute("UPDATE candidate_jobs SET lease_expires_at = now() + interval '1 second' WHERE id = %s", (job_id,))
    ok = candidate_claim.heartbeat(conn, job_id, claimed.generation, claimed.token)
    assert ok is True
    after = conn.execute("SELECT lease_expires_at FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert after > before


def test_heartbeat_fails_on_stale_generation(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    conn.execute("UPDATE candidate_jobs SET generation = generation + 1 WHERE id = %s", (job_id,))
    ok = candidate_claim.heartbeat(conn, job_id, claimed.generation, claimed.token)
    assert ok is False
