"""Full AD-6 lease/fencing claim loop for `start_preparations` (Story 4.1).

Retrofits the generation-counter/high-entropy-token/heartbeat/CAS-fencing
protocol proven in `job_lease.py`'s Story 1.5a proof harness onto this
production table, replacing Story 3.5's minimal-CAS claim (bare `status`
transitions with no lease). Every worker mutation now proves job id,
generation, token, and an active lease (AD-6) -- not status alone.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from psycopg import Connection

MAX_ATTEMPTS = 2
LEASE_SECONDS = 12


@dataclass(frozen=True)
class ClaimedPreparation:
    id: str
    analysis_session_id: str
    job_description_version: int
    attempt: int
    generation: int
    token: str


def claim_queued(conn: Connection) -> ClaimedPreparation | None:
    """Atomically claim one queued preparation (CAS, not read-then-write),
    granting a fresh generation/token/12-second lease."""
    token = secrets.token_hex(16)
    with conn.transaction():
        row = conn.execute(
            f"""
            UPDATE start_preparations
            SET status = 'deriving',
                generation = generation + 1,
                lease_token = %s,
                lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE id = (
                SELECT id FROM start_preparations
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, analysis_session_id, job_description_version, attempt, generation
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        prep_id, analysis_session_id, job_description_version, attempt, generation = row
        return ClaimedPreparation(
            id=str(prep_id),
            analysis_session_id=str(analysis_session_id),
            job_description_version=job_description_version,
            attempt=attempt,
            generation=generation,
            token=token,
        )


def heartbeat(conn: Connection, preparation_id: str, generation: int, token: str) -> bool:
    """Extend the lease. Fails (returns False, changes nothing) if
    generation/token/active-lease no longer match -- the fencing check
    every worker mutation must pass (AD-6)."""
    with conn.transaction():
        cur = conn.execute(
            f"""
            UPDATE start_preparations
            SET lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE id = %s AND generation = %s AND lease_token = %s
              AND status = 'deriving' AND lease_expires_at > now()
            """,
            (preparation_id, generation, token),
        )
        return cur.rowcount == 1


class DanglingPreparationError(Exception):
    """Raised when a claimed preparation's analysis_session_id has no
    matching row — an integrity problem, not an "empty Job Description"
    (review finding: silently returning "" masked this)."""


def fetch_job_description_text(conn: Connection, analysis_session_id: str) -> str:
    row = conn.execute(
        "SELECT job_description_text FROM analysis_sessions WHERE id = %s",
        (analysis_session_id,),
    ).fetchone()
    if row is None:
        raise DanglingPreparationError(f"no analysis_sessions row for id={analysis_session_id!r}")
    return row[0]


def stage_success(
    conn: Connection, preparation_id: str, generation: int, token: str, proposal_json: dict
) -> bool:
    """CAS: only succeeds if this row is still the one we claimed, fenced on
    generation+token+active lease (AC#3: a reclaimed/expired worker's late
    write is rejected without changing state)."""
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE start_preparations
            SET status = 'validated', proposal_json = %s, state_version = state_version + 1
            WHERE id = %s AND generation = %s AND lease_token = %s
              AND status = 'deriving' AND lease_expires_at > now()
            """,
            (json.dumps(proposal_json), preparation_id, generation, token),
        )
        return cur.rowcount == 1


def stage_failure(
    conn: Connection,
    preparation_id: str,
    analysis_session_id: str,
    attempt: int,
    generation: int,
    token: str,
    failure_reason: str,
) -> bool:
    """CAS: requeue for attempt 2 if attempts remain, else terminate failed AND
    unlock the session in the same transaction (AC#2's "inputs unlock only
    when the preparation is terminal"). Fenced on generation+token+active
    lease like `stage_success` (AC#3)."""
    reason = failure_reason[:64]
    with conn.transaction():
        if attempt < MAX_ATTEMPTS:
            cur = conn.execute(
                """
                UPDATE start_preparations
                SET status = 'queued', attempt = attempt + 1, failure_reason = %s,
                    state_version = state_version + 1,
                    lease_token = NULL, lease_expires_at = NULL
                WHERE id = %s AND generation = %s AND lease_token = %s
                  AND status = 'deriving' AND lease_expires_at > now()
                """,
                (reason, preparation_id, generation, token),
            )
        else:
            cur = conn.execute(
                """
                UPDATE start_preparations
                SET status = 'failed', failure_reason = %s, state_version = state_version + 1
                WHERE id = %s AND generation = %s AND lease_token = %s
                  AND status = 'deriving' AND lease_expires_at > now()
                """,
                (reason, preparation_id, generation, token),
            )
            if cur.rowcount == 1:
                conn.execute(
                    """
                    UPDATE analysis_sessions
                    SET status = 'draft'
                    WHERE id = %s AND status = 'preparing_to_start'
                    """,
                    (analysis_session_id,),
                )
        return cur.rowcount == 1
