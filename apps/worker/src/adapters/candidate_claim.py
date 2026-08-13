"""Full AD-6 lease/fencing claim mechanics for `candidate_jobs` (Story
4.1), mirroring `preparation_claim.py`'s claim/heartbeat shape against this
table's actual columns.

Deliberately no `stage_success`/`stage_failure` here -- what "success"
means for a Candidate job (a persisted parse artifact plus provider
proposal, AD-8) is Story 4.2+'s scope to define. This module proves only
that a `candidate_jobs` row can be safely claimed, held, heartbeated, and
recovered; nothing in production calls `claim_queued` yet (see Story 4.1's
Dev Notes for why wiring an idle poll loop here would be premature).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from psycopg import Connection

from .preparation_claim import LEASE_SECONDS


@dataclass(frozen=True)
class ClaimedCandidateJob:
    id: str
    analysis_revision_id: str
    candidate_id: str
    attempt: int
    generation: int
    token: str


def claim_queued(conn: Connection) -> ClaimedCandidateJob | None:
    """Atomically claim one queued Candidate job (CAS, not read-then-write),
    granting a fresh generation/token/12-second lease."""
    token = secrets.token_hex(16)
    with conn.transaction():
        row = conn.execute(
            f"""
            UPDATE candidate_jobs
            SET status = 'claimed',
                generation = generation + 1,
                lease_token = %s,
                lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE id = (
                SELECT id FROM candidate_jobs
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, analysis_revision_id, candidate_id, attempt, generation
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        job_id, analysis_revision_id, candidate_id, attempt, generation = row
        return ClaimedCandidateJob(
            id=str(job_id),
            analysis_revision_id=str(analysis_revision_id),
            candidate_id=str(candidate_id),
            attempt=attempt,
            generation=generation,
            token=token,
        )


def heartbeat(conn: Connection, job_id: str, generation: int, token: str) -> bool:
    """Extend the lease. Fails (returns False, changes nothing) if
    generation/token/active-lease no longer match (AD-6 fencing)."""
    with conn.transaction():
        cur = conn.execute(
            f"""
            UPDATE candidate_jobs
            SET lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE id = %s AND generation = %s AND lease_token = %s
              AND status = 'claimed' AND lease_expires_at > now()
            """,
            (job_id, generation, token),
        )
        return cur.rowcount == 1
