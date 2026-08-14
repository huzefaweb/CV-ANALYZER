"""Full AD-6 lease/fencing claim mechanics for `question_set_jobs` (Story
7.1), mirroring `candidate_claim.py`'s claim/heartbeat/stage shape but
simpler — no parse phase, since the grounding data (Job Requirements, the
Candidate's already-staged Evidence proposal, source-unit locators) already
exists from the Candidate's prior successful analysis run. One attempt
either stages a fenced `question_set_proposals` row or requeues/terminates
under the same AR-16 "attempt 1 plus at most attempt 2" budget every other
job type in this codebase uses.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass

from psycopg import Connection

from .preparation_claim import LEASE_SECONDS, MAX_ATTEMPTS


@dataclass(frozen=True)
class ClaimedQuestionSetJob:
    id: str
    candidate_id: str
    analysis_revision_id: str
    attempt: int
    generation: int
    token: str


def claim_queued(conn: Connection) -> ClaimedQuestionSetJob | None:
    """Atomically claim one queued Question Set job (CAS, not
    read-then-write), granting a fresh generation/token/12-second lease."""
    token = secrets.token_hex(16)
    with conn.transaction():
        row = conn.execute(
            f"""
            UPDATE question_set_jobs
            SET status = 'claimed',
                generation = generation + 1,
                lease_token = %s,
                lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE id = (
                SELECT id FROM question_set_jobs
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, candidate_id, analysis_revision_id, attempt, generation
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        job_id, candidate_id, analysis_revision_id, attempt, generation = row
        return ClaimedQuestionSetJob(
            id=str(job_id),
            candidate_id=str(candidate_id),
            analysis_revision_id=str(analysis_revision_id),
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
            UPDATE question_set_jobs
            SET lease_expires_at = now() + interval '{LEASE_SECONDS} seconds'
            WHERE id = %s AND generation = %s AND lease_token = %s
              AND status = 'claimed' AND lease_expires_at > now()
            """,
            (job_id, generation, token),
        )
        return cur.rowcount == 1


def stage_success(
    conn: Connection,
    job_id: str,
    generation: int,
    token: str,
    *,
    candidate_id: str,
    analysis_revision_id: str,
    items_json: list,
) -> bool:
    """CAS: fenced on generation+token+active lease, requiring `status =
    'claimed'`. The `question_set_proposals` insert is `ON CONFLICT
    (question_set_job_id) DO NOTHING` — a duplicate fenced write for the same
    job (e.g. re-delivered after a sweep reclaim) creates no second proposal
    (AR-16: "only one fenced proposal... may commit")."""
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE question_set_jobs
            SET status = 'completed', state_version = state_version + 1
            WHERE id = %s AND generation = %s AND lease_token = %s
              AND status = 'claimed' AND lease_expires_at > now()
            """,
            (job_id, generation, token),
        )
        if cur.rowcount != 1:
            return False
        conn.execute(
            """
            INSERT INTO question_set_proposals (
                id, question_set_job_id, candidate_id, analysis_revision_id,
                items_json, created_at
            )
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (question_set_job_id) DO NOTHING
            """,
            (
                str(uuid.uuid4()),
                job_id,
                candidate_id,
                analysis_revision_id,
                json.dumps(items_json),
            ),
        )
        return True


def stage_failure(
    conn: Connection,
    job_id: str,
    attempt: int,
    generation: int,
    token: str,
    failure_reason: str,
) -> bool:
    """CAS: requeue for another attempt if attempts remain (AR-16, `MAX_
    ATTEMPTS = 2`), else terminate `'failed'`. Fenced on
    generation+token+active lease requiring `status = 'claimed'`, same shape
    as `candidate_claim.stage_provider_failure`."""
    reason = failure_reason[:64]
    with conn.transaction():
        if attempt < MAX_ATTEMPTS:
            cur = conn.execute(
                """
                UPDATE question_set_jobs
                SET status = 'queued', attempt = attempt + 1, failure_reason = %s,
                    state_version = state_version + 1, reclaim_count = 0,
                    lease_token = NULL, lease_expires_at = NULL
                WHERE id = %s AND generation = %s AND lease_token = %s
                  AND status = 'claimed' AND lease_expires_at > now()
                """,
                (reason, job_id, generation, token),
            )
        else:
            cur = conn.execute(
                """
                UPDATE question_set_jobs
                SET status = 'failed', failure_reason = %s, state_version = state_version + 1
                WHERE id = %s AND generation = %s AND lease_token = %s
                  AND status = 'claimed' AND lease_expires_at > now()
                """,
                (reason, job_id, generation, token),
            )
        return cur.rowcount == 1


def fetch_grounded_data(
    conn: Connection, candidate_id: str, analysis_revision_id: str
) -> tuple[dict[str, str], list[dict], dict[str, dict | None]]:
    """Reads the raw rows `question_context.build_grounded_context` needs:
    `(requirement_texts, proposal_items, unit_locators)`. A thin DB-read
    adapter (matching this codebase's app/domain split — raw SQL stays in
    `adapters/`, pure transformation stays in `domain/`), mirroring
    `main.py::_fetch_job_requirements`'s join shape and `workspace.py::
    candidate_report`'s existing candidate_results -> candidate_proposals /
    candidate -> document -> parse_artifacts joins gateway-side.

    Code review fix (Blind Hunter, High): keys `requirement_texts` on
    `jr.display_id` (e.g. "JR-001"), NOT `jr.id` (the raw UUID primary
    key). `main.py::_fetch_job_requirements`'s own docstring is explicit
    that `display_id` is "the exact id the provider adapter's user message
    already sends/expects" during the original Candidate analysis — so
    `candidate_proposals.items_json[*].job_requirement_id` (echoed back by
    the provider from that same prompt) is a `display_id`, never the UUID.
    Keying on `jr.id` here meant every lookup below would silently or
    (after the hard-subscript fix) loudly fail to match, because the two
    id-spaces never align."""
    requirement_rows = conn.execute(
        """
        SELECT jr.display_id, jr.canonical_text
        FROM job_requirements jr
        JOIN analysis_revisions ar ON ar.analysis_session_id = jr.analysis_session_id
        WHERE ar.id = %s
        """,
        (analysis_revision_id,),
    ).fetchall()
    requirement_texts = {str(row[0]): row[1] for row in requirement_rows}

    proposal_row = conn.execute(
        """
        SELECT cp.items_json
        FROM candidate_results cr
        JOIN candidate_proposals cp ON cp.candidate_job_id = cr.candidate_job_id
        WHERE cr.candidate_id = %s AND cr.analysis_revision_id = %s
        """,
        (candidate_id, analysis_revision_id),
    ).fetchone()
    proposal_items: list[dict] = list(proposal_row[0]) if proposal_row is not None else []

    locator_row = conn.execute(
        """
        SELECT pa.source_units_json
        FROM candidates c
        JOIN documents d ON d.id = c.document_id
        JOIN parse_artifacts pa ON pa.document_id = d.id AND pa.document_content_version = d.content_version
        WHERE c.id = %s
        ORDER BY pa.created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    unit_locators: dict[str, dict | None] = {}
    if locator_row is not None:
        for unit in locator_row[0]:
            unit_locators[str(unit["id"])] = unit.get("locator")

    return requirement_texts, proposal_items, unit_locators
