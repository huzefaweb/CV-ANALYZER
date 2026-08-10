"""Harness-only lease/fencing/finalize primitives proving AD-6/AR-14-AR-16
against real PostgreSQL (Story 1.5a).

Not the production job-claiming feature -- Story 4.1 owns that. Every
timing decision (`lease_expires_at`, `now()`) uses PostgreSQL server time,
never Python's clock, per AD-6/SOLUTION-DESIGN.md §8.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from psycopg import Connection

LEASE_SECONDS = 12
HEARTBEAT_SECONDS = 4
SWEEP_SECONDS = 2
MAX_RECLAIMS_PER_ATTEMPT = 1
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ClaimResult:
    job_pk: str
    generation: int
    token: str
    state_version: int


def claim(conn: Connection, logical_id: str) -> ClaimResult | None:
    """Claim one queued (or previously-reclaimed-but-still-queued) row for
    `logical_id` using FOR UPDATE SKIP LOCKED. Processing must happen
    outside this transaction (AD-6)."""
    with conn.transaction():
        row = conn.execute(
            """
            SELECT job_pk, generation, state_version
            FROM job_lease_proof
            WHERE logical_id = %s AND status = 'queued'
            ORDER BY attempt DESC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (logical_id,),
        ).fetchone()
        if row is None:
            return None
        job_pk, generation, state_version = row
        new_generation = generation + 1
        token = secrets.token_hex(16)
        conn.execute(
            f"""
            UPDATE job_lease_proof
            SET generation = %s,
                token = %s,
                status = 'claimed',
                lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE job_pk = %s
            """,
            (new_generation, token, job_pk),
        )
        return ClaimResult(job_pk=str(job_pk), generation=new_generation, token=token, state_version=state_version)


def heartbeat(conn: Connection, job_pk: str, generation: int, token: str) -> bool:
    """Extend the lease. Fails (returns False, changes nothing) if
    generation/token/active-lease no longer match -- the fencing check
    every worker mutation must pass (AD-6)."""
    with conn.transaction():
        cur = conn.execute(
            f"""
            UPDATE job_lease_proof
            SET lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE job_pk = %s AND generation = %s AND token = %s
              AND status = 'claimed' AND lease_expires_at > now()
            """,
            (job_pk, generation, token),
        )
        return cur.rowcount == 1


def stage_result(
    conn: Connection, job_pk: str, generation: int, token: str, expected_state_version: int
) -> int | None:
    """Simulate the worker's fenced staging transaction (SOLUTION-DESIGN.md
    §8): advance to awaiting_finalization only if generation+token+active
    lease+expected state_version all still match. Returns the new
    state_version on success (the value `finalize`'s expected_state_version
    must then use), or None on a fencing/CAS miss -- callers never need to
    reconstruct "+1" themselves."""
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE job_lease_proof
            SET status = 'awaiting_finalization', state_version = state_version + 1
            WHERE job_pk = %s AND generation = %s AND token = %s
              AND status = 'claimed' AND lease_expires_at > now()
              AND state_version = %s
            RETURNING state_version
            """,
            (job_pk, generation, token, expected_state_version),
        )
        row = cur.fetchone()
        return row[0] if row is not None else None


def sweep_stale(conn: Connection) -> list[str]:
    """Reclaim or exhaust every stale-leased row (AR-15/AR-16). Returns the
    job_pks touched. Safe to call repeatedly (immediate startup scan, ≤2s
    periodic scan, see SWEEP_SECONDS).

    ponytail: one transaction covers the whole stale-row batch, so a single
    row's constraint violation aborts reclaim/exhaustion progress for every
    other stale row in that call. Acceptable for this proof harness's
    single-caller tests; Story 4.1's real sweep should isolate each row
    (savepoint or one transaction per row) if it processes many jobs at once.
    """
    touched: list[str] = []
    with conn.transaction():
        stale = conn.execute(
            """
            SELECT job_pk, logical_id, attempt, reclaim_count
            FROM job_lease_proof
            WHERE status = 'claimed' AND lease_expires_at <= now()
            FOR UPDATE SKIP LOCKED
            """
        ).fetchall()
        for job_pk, logical_id, attempt, reclaim_count in stale:
            if reclaim_count < MAX_RECLAIMS_PER_ATTEMPT:
                token = secrets.token_hex(16)
                conn.execute(
                    f"""
                    UPDATE job_lease_proof
                    SET generation = generation + 1,
                        token = %s,
                        reclaim_count = reclaim_count + 1,
                        lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
                    WHERE job_pk = %s
                    """,
                    (token, job_pk),
                )
            elif attempt < MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE job_lease_proof SET status = 'failed' WHERE job_pk = %s",
                    (job_pk,),
                )
                conn.execute(
                    """
                    INSERT INTO job_lease_proof (logical_id, attempt, status)
                    VALUES (%s, %s, 'queued')
                    """,
                    (logical_id, attempt + 1),
                )
            else:
                conn.execute(
                    "UPDATE job_lease_proof SET status = 'failed' WHERE job_pk = %s",
                    (job_pk,),
                )
            touched.append(str(job_pk))
    return touched


def finalize(conn: Connection, job_pk: str, expected_state_version: int) -> bool:
    """Publication-style CAS (AD-7): commits exactly once for a given
    expected_state_version. A second call with the same expected version
    is a no-op (idempotent replay); a stale expected version fails the CAS
    (no regressing effect).

    Deliberately takes no generation/token: per SOLUTION-DESIGN.md §8 the
    gateway-owned finalizer is a stateless, level-triggered scan over
    immutable staged data, not a lease-holding worker mutation -- by the
    time a row reaches awaiting_finalization, the worker's lease is no
    longer the reauthorization mechanism; expected_state_version's CAS is.
    This is the same "no persistent claim can go stale" pattern AD-7
    describes for the real publication coordinator."""
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE job_lease_proof
            SET status = 'done', state_version = state_version + 1
            WHERE job_pk = %s AND state_version = %s AND status = 'awaiting_finalization'
            """,
            (job_pk, expected_state_version),
        )
        return cur.rowcount == 1
