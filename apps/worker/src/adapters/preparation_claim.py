"""Minimal-CAS claim loop for `start_preparations` (Story 3.5, AD-4).

Deliberately NOT the full AD-6 lease/fencing protocol (generation counter,
high-entropy token, heartbeat, recovery sweep) — that is Story 4.1's scope.
This is a bare CAS status transition (`queued -> deriving -> validated /
queued(attempt+1) / failed`), sufficient for V1's single-worker,
single-demo-host scale. See 3-5's Dev Notes for the full reasoning and the
disclosed gap (a crash mid-`deriving` leaves the row stuck with no recovery
sweep until Story 4.1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from psycopg import Connection

MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ClaimedPreparation:
    id: str
    analysis_session_id: str
    job_description_version: int
    attempt: int


def claim_queued(conn: Connection) -> ClaimedPreparation | None:
    """Atomically claim one queued preparation (CAS, not read-then-write)."""
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE start_preparations
            SET status = 'deriving'
            WHERE id = (
                SELECT id FROM start_preparations
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, analysis_session_id, job_description_version, attempt
            """
        ).fetchone()
        if row is None:
            return None
        prep_id, analysis_session_id, job_description_version, attempt = row
        return ClaimedPreparation(
            id=str(prep_id),
            analysis_session_id=str(analysis_session_id),
            job_description_version=job_description_version,
            attempt=attempt,
        )


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


def stage_success(conn: Connection, preparation_id: str, proposal_json: dict) -> bool:
    """CAS: only succeeds if this row is still the one we claimed."""
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE start_preparations
            SET status = 'validated', proposal_json = %s
            WHERE id = %s AND status = 'deriving'
            """,
            (json.dumps(proposal_json), preparation_id),
        )
        return cur.rowcount == 1


def stage_failure(
    conn: Connection, preparation_id: str, analysis_session_id: str, attempt: int, failure_reason: str
) -> bool:
    """CAS: requeue for attempt 2 if attempts remain, else terminate failed AND
    unlock the session in the same transaction (review finding: AC#2 requires
    "inputs unlock only when the preparation is terminal" — terminal failure
    on the worker side is one such terminus, not just the gateway's recheck
    failures; without this the session stayed locked forever after attempt 2
    was exhausted)."""
    reason = failure_reason[:64]
    with conn.transaction():
        if attempt < MAX_ATTEMPTS:
            cur = conn.execute(
                """
                UPDATE start_preparations
                SET status = 'queued', attempt = attempt + 1, failure_reason = %s
                WHERE id = %s AND status = 'deriving'
                """,
                (reason, preparation_id),
            )
        else:
            cur = conn.execute(
                """
                UPDATE start_preparations
                SET status = 'failed', failure_reason = %s
                WHERE id = %s AND status = 'deriving'
                """,
                (reason, preparation_id),
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
