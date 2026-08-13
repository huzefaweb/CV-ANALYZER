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
    connection.execute(
        "TRUNCATE TABLE candidate_jobs, parse_artifacts, candidate_identities RESTART IDENTITY CASCADE"
    )
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


def _stage_parse_success_kwargs(candidate_id: str, document_id: str) -> dict:
    return dict(
        candidate_id=candidate_id,
        document_id=document_id,
        document_content_version=1,
        parser_pipeline_version="v1",
        source_units_json=[{"id": "u1", "text": "Backend engineer.", "locator": None}],
        blocks_json=[{"text": "Backend engineer.", "content_class": "employment"}],
        gate_codes=[],
        coherent_block_count=2,
        display_name="Jordan Rivera",
        name_source="parsed",
        email="jordan@example.com",
        phone=None,
    )


def test_stage_parse_success_persists_artifact_and_identity_and_advances_status(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    candidate_id = claimed.candidate_id
    document_id = str(uuid.uuid4())

    ok = candidate_claim.stage_parse_success(
        conn, job_id, claimed.generation, claimed.token, **_stage_parse_success_kwargs(candidate_id, document_id)
    )
    assert ok is True

    status = conn.execute("SELECT status FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "parsed"

    artifact = conn.execute(
        "SELECT candidate_id, document_content_version, parser_pipeline_version FROM parse_artifacts WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    assert artifact == (candidate_id, 1, "v1")

    identity = conn.execute(
        "SELECT display_name, name_source, email, phone FROM candidate_identities WHERE candidate_id = %s",
        (candidate_id,),
    ).fetchone()
    assert identity == ("Jordan Rivera", "parsed", "jordan@example.com", None)


def test_stage_parse_success_duplicate_delivery_does_not_duplicate_artifact(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    candidate_id = claimed.candidate_id
    document_id = str(uuid.uuid4())
    kwargs = _stage_parse_success_kwargs(candidate_id, document_id)

    candidate_claim.stage_parse_success(conn, job_id, claimed.generation, claimed.token, **kwargs)
    # Re-arm the job as if reclaimed and re-claimed under a fresh fence, then
    # deliver the identical parse result again (duplicate delivery, AC#3).
    conn.execute(
        "UPDATE candidate_jobs SET status = 'claimed', generation = generation + 1, "
        "lease_token = 'dup-token', lease_expires_at = now() + interval '12 seconds' WHERE id = %s",
        (job_id,),
    )
    new_generation = conn.execute("SELECT generation FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]

    ok = candidate_claim.stage_parse_success(conn, job_id, new_generation, "dup-token", **kwargs)
    assert ok is True

    count = conn.execute(
        "SELECT COUNT(*) FROM parse_artifacts WHERE document_id = %s", (document_id,)
    ).fetchone()[0]
    assert count == 1


def test_stage_parse_success_rejects_stale_fence_and_changes_nothing(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    candidate_id = claimed.candidate_id
    document_id = str(uuid.uuid4())
    conn.execute("UPDATE candidate_jobs SET generation = generation + 1 WHERE id = %s", (job_id,))

    ok = candidate_claim.stage_parse_success(
        conn, job_id, claimed.generation, claimed.token, **_stage_parse_success_kwargs(candidate_id, document_id)
    )
    assert ok is False

    status = conn.execute("SELECT status FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "claimed"
    count = conn.execute(
        "SELECT COUNT(*) FROM parse_artifacts WHERE document_id = %s", (document_id,)
    ).fetchone()[0]
    assert count == 0


def test_stage_parse_failure_requeues_on_attempt_1(conn):
    job_id = _seed_candidate_job(conn, attempt=1)
    claimed = candidate_claim.claim_queued(conn)
    ok = candidate_claim.stage_parse_failure(
        conn, job_id, claimed.attempt, claimed.generation, claimed.token, "corrupt PDF container"
    )
    assert ok is True
    row = conn.execute(
        "SELECT status, attempt, failure_reason, lease_token FROM candidate_jobs WHERE id = %s", (job_id,)
    ).fetchone()
    assert row == ("queued", 2, "corrupt PDF container", None)


def test_stage_parse_failure_terminates_after_max_attempts(conn):
    job_id = _seed_candidate_job(conn, attempt=2)
    claimed = candidate_claim.claim_queued(conn)
    ok = candidate_claim.stage_parse_failure(
        conn, job_id, claimed.attempt, claimed.generation, claimed.token, "corrupt PDF container"
    )
    assert ok is True
    status = conn.execute("SELECT status FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "failed"


def test_stage_parse_failure_rejects_stale_fence(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    conn.execute("UPDATE candidate_jobs SET generation = generation + 1 WHERE id = %s", (job_id,))
    ok = candidate_claim.stage_parse_failure(
        conn, job_id, claimed.attempt, claimed.generation, claimed.token, "corrupt PDF container"
    )
    assert ok is False
    status = conn.execute("SELECT status FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "claimed"


def _rearm_job(conn, job_id: str, token: str) -> int:
    """Simulate a second worker attempt re-claiming the same job (a real
    re-parse), returning the new generation."""
    conn.execute(
        "UPDATE candidate_jobs SET status = 'claimed', generation = generation + 1, "
        "lease_token = %s, lease_expires_at = now() + interval '12 seconds' WHERE id = %s",
        (token, job_id),
    )
    return conn.execute("SELECT generation FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]


def test_stage_parse_success_upsert_refreshes_identity_on_reparse(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    candidate_id = claimed.candidate_id
    document_id = str(uuid.uuid4())

    first = _stage_parse_success_kwargs(candidate_id, document_id)
    candidate_claim.stage_parse_success(conn, job_id, claimed.generation, claimed.token, **first)

    generation = _rearm_job(conn, job_id, "reparse-token")
    second = _stage_parse_success_kwargs(candidate_id, str(uuid.uuid4()))
    second["display_name"] = "Jordan A. Rivera"
    second["email"] = "jordan.rivera@example.com"
    candidate_claim.stage_parse_success(conn, job_id, generation, "reparse-token", **second)

    identity = conn.execute(
        "SELECT display_name, name_source, email FROM candidate_identities WHERE candidate_id = %s",
        (candidate_id,),
    ).fetchone()
    assert identity == ("Jordan A. Rivera", "parsed", "jordan.rivera@example.com")


def test_stage_parse_success_does_not_regress_parsed_name_to_fallback(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    candidate_id = claimed.candidate_id
    document_id = str(uuid.uuid4())

    first = _stage_parse_success_kwargs(candidate_id, document_id)
    candidate_claim.stage_parse_success(conn, job_id, claimed.generation, claimed.token, **first)

    generation = _rearm_job(conn, job_id, "reparse-token")
    second = _stage_parse_success_kwargs(candidate_id, str(uuid.uuid4()))
    second["display_name"] = "DOC-002 — resume.pdf"
    second["name_source"] = "fallback"
    second["email"] = None
    candidate_claim.stage_parse_success(conn, job_id, generation, "reparse-token", **second)

    identity = conn.execute(
        "SELECT display_name, name_source, email FROM candidate_identities WHERE candidate_id = %s",
        (candidate_id,),
    ).fetchone()
    # Name stays the genuinely parsed one; email stays the previously known
    # value rather than being nulled out by an attempt that found none.
    assert identity == ("Jordan Rivera", "parsed", "jordan@example.com")


def test_stage_parse_success_truncates_oversized_display_name(conn):
    job_id = _seed_candidate_job(conn)
    claimed = candidate_claim.claim_queued(conn)
    candidate_id = claimed.candidate_id
    kwargs = _stage_parse_success_kwargs(candidate_id, str(uuid.uuid4()))
    kwargs["display_name"] = "x" * 300
    kwargs["name_source"] = "fallback"

    ok = candidate_claim.stage_parse_success(conn, job_id, claimed.generation, claimed.token, **kwargs)
    assert ok is True
    display_name = conn.execute(
        "SELECT display_name FROM candidate_identities WHERE candidate_id = %s", (candidate_id,)
    ).fetchone()[0]
    assert len(display_name) == 255
