"""Real-PostgreSQL restart/recovery proof harness (Story 1.5a).

Skipped (not failed) when WORKER_DATABASE_URL is unset — mirrors Story 1.2's
apps/gateway/tests/conftest.py pattern: talk to a real PostgreSQL instance,
never fake it with SQLite/mocks (Story 1.1's PostgreSQL-only invariant).
Precondition: `docker compose up -d postgres`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import psycopg
import pytest

from src.adapters import job_lease

WORKER_DATABASE_URL = os.environ.get("WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not WORKER_DATABASE_URL,
    reason="WORKER_DATABASE_URL not set; restart-recovery tests require a live PostgreSQL instance",
)

SCHEMA_SQL = (Path(__file__).parent / "fixtures" / "job_lease_schema.sql").read_text(encoding="utf-8")


@pytest.fixture()
def conn():
    connection = psycopg.connect(WORKER_DATABASE_URL, autocommit=True)
    connection.execute(SCHEMA_SQL)
    connection.execute("TRUNCATE TABLE job_lease_proof")
    yield connection
    connection.close()


def _seed(conn, logical_id: str) -> None:
    conn.execute(
        "INSERT INTO job_lease_proof (logical_id, status) VALUES (%s, 'queued')",
        (logical_id,),
    )


def _backdate_expired(conn, job_pk: str) -> None:
    conn.execute(
        "UPDATE job_lease_proof SET lease_expires_at = now() - interval '1 second' WHERE job_pk = %s",
        (job_pk,),
    )


def test_claim_then_heartbeat_succeeds_and_blocks_a_second_claim(conn):
    _seed(conn, "job-a")
    claimed = job_lease.claim(conn, "job-a")
    assert claimed is not None
    assert job_lease.heartbeat(conn, claimed.job_pk, claimed.generation, claimed.token) is True

    second = job_lease.claim(conn, "job-a")
    assert second is None, "an active lease must not be claimable a second time"


def test_heartbeat_with_stale_generation_or_token_fails_fencing(conn):
    _seed(conn, "job-b")
    claimed = job_lease.claim(conn, "job-b")

    assert job_lease.heartbeat(conn, claimed.job_pk, claimed.generation - 1, claimed.token) is False
    assert job_lease.heartbeat(conn, claimed.job_pk, claimed.generation, "wrong-token") is False

    row = conn.execute(
        "SELECT lease_expires_at FROM job_lease_proof WHERE job_pk = %s", (claimed.job_pk,)
    ).fetchone()
    assert row is not None  # fenced calls must not have raised or corrupted the row


def test_stale_lease_is_reclaimed_exactly_once(conn):
    _seed(conn, "job-c")
    claimed = job_lease.claim(conn, "job-c")
    _backdate_expired(conn, claimed.job_pk)

    touched = job_lease.sweep_stale(conn)
    assert claimed.job_pk in touched

    row = conn.execute(
        "SELECT generation, token, reclaim_count, status FROM job_lease_proof WHERE job_pk = %s",
        (claimed.job_pk,),
    ).fetchone()
    generation, token, reclaim_count, status = row
    assert generation == claimed.generation + 1
    assert reclaim_count == 1
    assert status == "claimed"

    # the old (pre-reclaim) generation/token is now stale and fenced out
    assert job_lease.heartbeat(conn, claimed.job_pk, claimed.generation, claimed.token) is False

    # but the reclaimed lease itself is fully usable -- not just a DB-row
    # artifact -- proving recovery actually restores a working lease
    assert job_lease.heartbeat(conn, claimed.job_pk, generation, token) is True

    # calling sweep_stale again immediately (lease not yet re-expired) must
    # be a no-op -- it must not re-reclaim an already-healthy lease
    assert claimed.job_pk not in job_lease.sweep_stale(conn)


def test_second_stale_lease_in_same_attempt_exhausts_to_attempt_two(conn):
    _seed(conn, "job-d")
    claimed = job_lease.claim(conn, "job-d")
    _backdate_expired(conn, claimed.job_pk)
    job_lease.sweep_stale(conn)  # first reclaim: reclaim_count 0 -> 1

    _backdate_expired(conn, claimed.job_pk)
    job_lease.sweep_stale(conn)  # second stale detection: exhausts attempt 1

    attempt_1 = conn.execute(
        "SELECT status FROM job_lease_proof WHERE job_pk = %s", (claimed.job_pk,)
    ).fetchone()
    assert attempt_1[0] == "failed"

    attempt_2 = conn.execute(
        "SELECT attempt, status, reclaim_count FROM job_lease_proof WHERE logical_id = %s AND attempt = 2",
        ("job-d",),
    ).fetchone()
    assert attempt_2 is not None, "attempt-1 exhaustion must atomically create attempt 2"
    assert attempt_2 == (2, "queued", 0)


def test_attempt_two_exhaustion_terminalizes_as_failed_with_no_third_attempt(conn):
    _seed(conn, "job-e")
    claimed = job_lease.claim(conn, "job-e")
    _backdate_expired(conn, claimed.job_pk)
    job_lease.sweep_stale(conn)
    _backdate_expired(conn, claimed.job_pk)
    job_lease.sweep_stale(conn)  # attempt 1 exhausted, attempt 2 created

    attempt_2 = job_lease.claim(conn, "job-e")
    assert attempt_2 is not None
    _backdate_expired(conn, attempt_2.job_pk)
    job_lease.sweep_stale(conn)
    _backdate_expired(conn, attempt_2.job_pk)
    job_lease.sweep_stale(conn)  # attempt 2 exhausted

    final_status = conn.execute(
        "SELECT status FROM job_lease_proof WHERE job_pk = %s", (attempt_2.job_pk,)
    ).fetchone()
    assert final_status[0] == "failed"

    third_attempt = conn.execute(
        "SELECT 1 FROM job_lease_proof WHERE logical_id = %s AND attempt = 3", ("job-e",)
    ).fetchone()
    assert third_attempt is None, "AR-16 permits attempt 1 plus at most attempt 2 -- never a third"


def test_finalize_is_idempotent_and_commits_exactly_once(conn):
    _seed(conn, "job-f")
    claimed = job_lease.claim(conn, "job-f")
    staged_version = job_lease.stage_result(conn, claimed.job_pk, claimed.generation, claimed.token, claimed.state_version)
    assert staged_version is not None, "stage_result must report the new state_version, not just True/False"

    first = job_lease.finalize(conn, claimed.job_pk, expected_state_version=staged_version)
    assert first is True

    second = job_lease.finalize(conn, claimed.job_pk, expected_state_version=staged_version)
    assert second is False, "duplicate finalize with the same expected version must be a no-op"

    row = conn.execute(
        "SELECT status, state_version FROM job_lease_proof WHERE job_pk = %s", (claimed.job_pk,)
    ).fetchone()
    assert row == ("done", staged_version + 1)


def test_finalize_with_stale_expected_version_fails_cas_and_changes_nothing(conn):
    _seed(conn, "job-g")
    claimed = job_lease.claim(conn, "job-g")
    job_lease.stage_result(conn, claimed.job_pk, claimed.generation, claimed.token, claimed.state_version)

    before = conn.execute(
        "SELECT status, state_version FROM job_lease_proof WHERE job_pk = %s", (claimed.job_pk,)
    ).fetchone()

    stale = job_lease.finalize(conn, claimed.job_pk, expected_state_version=claimed.state_version)
    assert stale is False, "a stale (already-superseded) expected_state_version must not commit"

    after = conn.execute(
        "SELECT status, state_version FROM job_lease_proof WHERE job_pk = %s", (claimed.job_pk,)
    ).fetchone()
    assert before == after, "a failed CAS must leave state completely unchanged"


def test_concurrent_claim_from_two_real_connections_never_double_claims(conn):
    """The one guarantee this story is named for: FOR UPDATE SKIP LOCKED
    under genuine concurrency, not just sequential re-claim attempts on one
    connection."""
    _seed(conn, "job-i")
    second_conn = psycopg.connect(WORKER_DATABASE_URL, autocommit=True)
    try:
        with conn.transaction():
            first_row = conn.execute(
                """
                SELECT job_pk FROM job_lease_proof
                WHERE logical_id = 'job-i' AND status = 'queued'
                FOR UPDATE SKIP LOCKED
                """
            ).fetchone()
            assert first_row is not None

            # a second real connection racing for the same row must be
            # skipped, not blocked -- proving SKIP LOCKED, not just that
            # sequential claim() calls see an already-claimed row
            second_result = second_conn.execute(
                """
                SELECT job_pk FROM job_lease_proof
                WHERE logical_id = 'job-i' AND status = 'queued'
                FOR UPDATE SKIP LOCKED
                """
            ).fetchone()
            assert second_result is None, "a concurrently-locked row must be skipped, not returned to a second claimant"
    finally:
        second_conn.close()


@pytest.mark.slow
def test_real_timing_recovery_stays_within_the_16_second_bound(conn):
    """The one deliberately slow fixture: proves the actual unconfigurable
    V1 constants (12s lease / 2s sweep cadence) using real elapsed time,
    per AR-15's 12 + 2 + 2 = 16-second worst-case bound."""
    _seed(conn, "job-h")
    claimed = job_lease.claim(conn, "job-h")

    started = time.monotonic()
    time.sleep(job_lease.LEASE_SECONDS + 0.5)  # let the real lease expire

    recovered = False
    deadline = started + 16.0
    while time.monotonic() < deadline:
        touched = job_lease.sweep_stale(conn)
        if claimed.job_pk in touched:
            recovered = True
            break
        time.sleep(2.0)  # AR-15's ≤2-second recovery-sweep cadence

    elapsed = time.monotonic() - started
    assert recovered, f"stale lease was not recovered within the 16-second bound (elapsed={elapsed:.1f}s)"
    print(f"[AF-13 evidence] real-timing recovery observed in {elapsed:.2f}s (bound: 16s)")


@pytest.mark.slow
def test_real_timing_heartbeat_at_the_4_second_cadence_keeps_the_lease_alive(conn):
    """Proves the other half of the 12/4-second configuration AC#1 requires:
    a worker heartbeating at HEARTBEAT_SECONDS keeps its lease alive well
    past LEASE_SECONDS, and sweep_stale never reclaims a live lease."""
    _seed(conn, "job-j")
    claimed = job_lease.claim(conn, "job-j")

    started = time.monotonic()
    beats = 0
    while time.monotonic() - started < job_lease.LEASE_SECONDS + 4.0:
        time.sleep(job_lease.HEARTBEAT_SECONDS)
        ok = job_lease.heartbeat(conn, claimed.job_pk, claimed.generation, claimed.token)
        assert ok, "heartbeat at the configured cadence must keep the lease alive"
        beats += 1

    touched = job_lease.sweep_stale(conn)
    assert claimed.job_pk not in touched, "a lease kept alive by on-cadence heartbeats must never be reclaimed"

    elapsed = time.monotonic() - started
    print(
        f"[AF-13 evidence] {beats} heartbeats at {job_lease.HEARTBEAT_SECONDS}s cadence kept the lease "
        f"alive for {elapsed:.2f}s (> {job_lease.LEASE_SECONDS}s lease)"
    )
