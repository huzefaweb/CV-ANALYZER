"""Real-PostgreSQL tests for the AD-6 lease/fencing claim mechanics on
`question_set_jobs` (Story 7.1), mirroring test_candidate_claim.py's
equivalent coverage shape against this simpler (no parse phase) table.
Skipped (not failed) when WORKER_DATABASE_URL is unset.
"""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from src.adapters import question_set_claim

WORKER_DATABASE_URL = os.environ.get("WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not WORKER_DATABASE_URL,
    reason="WORKER_DATABASE_URL not set; requires a live PostgreSQL instance with gateway migrations applied",
)


@pytest.fixture()
def conn():
    connection = psycopg.connect(WORKER_DATABASE_URL, autocommit=True)
    connection.execute(
        "TRUNCATE TABLE question_set_jobs, question_set_proposals, "
        "job_requirements, analysis_revisions, candidate_proposals, candidate_results, "
        "candidates, documents, parse_artifacts, analysis_sessions "
        "RESTART IDENTITY CASCADE"
    )
    yield connection
    connection.close()


def _seed_job(conn, *, attempt: int = 1) -> tuple[str, str, str]:
    job_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    analysis_revision_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO question_set_jobs (
            id, candidate_id, analysis_revision_id, status, idempotency_key,
            created_at, attempt
        )
        VALUES (%s, %s, %s, 'queued', 'key-1', now(), %s)
        """,
        (job_id, candidate_id, analysis_revision_id, attempt),
    )
    return job_id, candidate_id, analysis_revision_id


def test_claim_transitions_queued_to_claimed_with_fresh_lease(conn):
    job_id, _, _ = _seed_job(conn)
    claimed = question_set_claim.claim_queued(conn)
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.generation == 1
    assert len(claimed.token) == 32
    row = conn.execute(
        "SELECT status, generation, lease_token, lease_expires_at > now() FROM question_set_jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row[0] == "claimed"
    assert row[1] == 1
    assert row[2] == claimed.token
    assert row[3] is True


def test_concurrent_claim_never_double_claims(conn):
    _seed_job(conn)

    def try_claim():
        with psycopg.connect(WORKER_DATABASE_URL, autocommit=True) as c:
            return question_set_claim.claim_queued(c)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: try_claim(), range(2)))

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1


def test_heartbeat_extends_lease_and_fails_on_stale_generation(conn):
    job_id, _, _ = _seed_job(conn)
    claimed = question_set_claim.claim_queued(conn)
    conn.execute(
        "UPDATE question_set_jobs SET lease_expires_at = now() + interval '1 second' WHERE id = %s", (job_id,)
    )
    assert question_set_claim.heartbeat(conn, job_id, claimed.generation, claimed.token) is True

    conn.execute("UPDATE question_set_jobs SET generation = generation + 1 WHERE id = %s", (job_id,))
    assert question_set_claim.heartbeat(conn, job_id, claimed.generation, claimed.token) is False


def test_stage_success_persists_proposal_and_advances_status(conn):
    job_id, candidate_id, analysis_revision_id = _seed_job(conn)
    claimed = question_set_claim.claim_queued(conn)
    items = [{"number": 1, "category": "technical_functional", "text": "?", "source_requirement_id": "JR-1"}]

    ok = question_set_claim.stage_success(
        conn, job_id, claimed.generation, claimed.token,
        candidate_id=candidate_id, analysis_revision_id=analysis_revision_id, items_json=items,
    )
    assert ok is True

    status = conn.execute("SELECT status FROM question_set_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "completed"

    row = conn.execute(
        "SELECT candidate_id, analysis_revision_id, items_json FROM question_set_proposals WHERE question_set_job_id = %s",
        (job_id,),
    ).fetchone()
    assert row[0] == candidate_id
    assert row[1] == analysis_revision_id
    assert row[2] == items


def test_stage_success_duplicate_delivery_does_not_duplicate_proposal(conn):
    job_id, candidate_id, analysis_revision_id = _seed_job(conn)
    claimed = question_set_claim.claim_queued(conn)
    kwargs = dict(candidate_id=candidate_id, analysis_revision_id=analysis_revision_id, items_json=[])

    question_set_claim.stage_success(conn, job_id, claimed.generation, claimed.token, **kwargs)
    conn.execute(
        "UPDATE question_set_jobs SET status = 'claimed', generation = generation + 1, "
        "lease_token = 'dup-token', lease_expires_at = now() + interval '12 seconds' WHERE id = %s",
        (job_id,),
    )
    new_generation = conn.execute("SELECT generation FROM question_set_jobs WHERE id = %s", (job_id,)).fetchone()[0]

    ok = question_set_claim.stage_success(conn, job_id, new_generation, "dup-token", **kwargs)
    assert ok is True

    count = conn.execute(
        "SELECT COUNT(*) FROM question_set_proposals WHERE question_set_job_id = %s", (job_id,)
    ).fetchone()[0]
    assert count == 1


def test_stage_success_rejects_stale_fence_and_changes_nothing(conn):
    job_id, candidate_id, analysis_revision_id = _seed_job(conn)
    claimed = question_set_claim.claim_queued(conn)
    conn.execute("UPDATE question_set_jobs SET generation = generation + 1 WHERE id = %s", (job_id,))

    ok = question_set_claim.stage_success(
        conn, job_id, claimed.generation, claimed.token,
        candidate_id=candidate_id, analysis_revision_id=analysis_revision_id, items_json=[],
    )
    assert ok is False
    status = conn.execute("SELECT status FROM question_set_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "claimed"
    count = conn.execute(
        "SELECT COUNT(*) FROM question_set_proposals WHERE question_set_job_id = %s", (job_id,)
    ).fetchone()[0]
    assert count == 0


def test_stage_failure_requeues_on_attempt_1(conn):
    job_id, _, _ = _seed_job(conn, attempt=1)
    claimed = question_set_claim.claim_queued(conn)
    ok = question_set_claim.stage_failure(conn, job_id, 1, claimed.generation, claimed.token, "Analysis timed out")
    assert ok is True
    row = conn.execute(
        "SELECT status, attempt, failure_reason, lease_token FROM question_set_jobs WHERE id = %s", (job_id,)
    ).fetchone()
    assert row == ("queued", 2, "Analysis timed out", None)


def test_stage_failure_terminates_after_max_attempts(conn):
    job_id, _, _ = _seed_job(conn, attempt=2)
    claimed = question_set_claim.claim_queued(conn)
    ok = question_set_claim.stage_failure(conn, job_id, 2, claimed.generation, claimed.token, "Analysis timed out")
    assert ok is True
    status = conn.execute("SELECT status FROM question_set_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "failed"


def test_stage_failure_rejects_stale_fence(conn):
    job_id, _, _ = _seed_job(conn)
    claimed = question_set_claim.claim_queued(conn)
    conn.execute("UPDATE question_set_jobs SET generation = generation + 1 WHERE id = %s", (job_id,))
    ok = question_set_claim.stage_failure(conn, job_id, 1, claimed.generation, claimed.token, "Analysis timed out")
    assert ok is False
    status = conn.execute("SELECT status FROM question_set_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "claimed"


def _seed_grounded_data(conn, candidate_id: str, analysis_revision_id: str) -> None:
    """Seeds the minimal cross-table data fetch_grounded_data reads: an
    analysis_session + job_requirement + analysis_revision, a candidate with
    a parsed document, and a completed candidate_job/candidate_proposal."""
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO analysis_sessions (id, creator_issuer, creator_subject, status, created_at) "
        "VALUES (%s, 'local', 'sub-1', 'frozen_inputs', now())",
        (session_id,),
    )
    conn.execute(
        "INSERT INTO analysis_revisions (id, analysis_session_id, revision_number, status, created_at) "
        "VALUES (%s, %s, 1, 'frozen', now())",
        (analysis_revision_id, session_id),
    )
    # Code review fix (Blind Hunter): `id` is a real UUID, distinct from
    # `display_id` — production job_requirements.id is always
    # str(uuid.uuid4()) (preparation_finalizer.py), never equal to its own
    # display_id. The original fixture set both to 'JR-1', which could not
    # have caught fetch_grounded_data keying requirement_texts on the wrong
    # column (a real bug this fixture change now exercises).
    requirement_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO job_requirements (id, analysis_session_id, display_id, component, classification, "
        "canonical_text, source_locators, created_at) "
        "VALUES (%s, %s, 'JR-001', 'mandatory_skills', 'mandatory', 'Kubernetes operations', '[]', now())",
        (requirement_id, session_id),
    )

    document_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO documents (id, analysis_session_id, document_reference, original_filename, "
        "content_version, storage_path, size_bytes, content_type, status, idempotency_key, created_at) "
        "VALUES (%s, %s, 'DOC-001', 'resume.pdf', 1, '/tmp/x', 10, 'application/pdf', 'ready', 'k', now())",
        (document_id, session_id),
    )
    conn.execute(
        "INSERT INTO candidates (id, analysis_session_id, document_id, document_reference, created_at) "
        "VALUES (%s, %s, %s, 'DOC-001', now())",
        (candidate_id, session_id, document_id),
    )
    conn.execute(
        "INSERT INTO parse_artifacts (id, candidate_id, document_id, document_content_version, "
        "parser_pipeline_version, source_units_json, blocks_json, gate_codes, coherent_block_count, created_at) "
        "VALUES (%s, %s, %s, 1, 'v1', %s, '[]', '[]', 1, now())",
        (
            str(uuid.uuid4()),
            candidate_id,
            document_id,
            json.dumps([{"id": "u1", "text": "Ran Kubernetes clusters.", "locator": None}]),
        ),
    )

    candidate_job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO candidate_jobs (id, analysis_revision_id, candidate_id, status, created_at) "
        "VALUES (%s, %s, %s, 'completed', now())",
        (candidate_job_id, analysis_revision_id, candidate_id),
    )
    conn.execute(
        "INSERT INTO candidate_proposals (id, candidate_job_id, candidate_id, analysis_revision_id, "
        "items_json, gate_codes, created_at) VALUES (%s, %s, %s, %s, %s, '[]', now())",
        (
            str(uuid.uuid4()),
            candidate_job_id,
            candidate_id,
            analysis_revision_id,
            # job_requirement_id is the display_id ("JR-001"), matching what
            # the provider actually echoes back from main.py's own
            # display_id-keyed prompt (_fetch_job_requirements) — never the
            # raw UUID primary key.
            json.dumps([{"job_requirement_id": "JR-001", "state": "Matched", "locator": "u1", "excerpt": "Kubernetes"}]),
        ),
    )
    conn.execute(
        "INSERT INTO candidate_results (id, analysis_revision_id, candidate_id, candidate_job_id, outcome, created_at) "
        "VALUES (%s, %s, %s, %s, 'NewResult', now())",
        (str(uuid.uuid4()), analysis_revision_id, candidate_id, candidate_job_id),
    )


def test_fetch_grounded_data_joins_requirements_proposal_and_locators(conn):
    candidate_id = str(uuid.uuid4())
    analysis_revision_id = str(uuid.uuid4())
    _seed_grounded_data(conn, candidate_id, analysis_revision_id)

    requirement_texts, proposal_items, unit_locators = question_set_claim.fetch_grounded_data(
        conn, candidate_id, analysis_revision_id
    )

    assert requirement_texts == {"JR-001": "Kubernetes operations"}
    assert proposal_items == [{"job_requirement_id": "JR-001", "state": "Matched", "locator": "u1", "excerpt": "Kubernetes"}]
    assert unit_locators == {"u1": None}
