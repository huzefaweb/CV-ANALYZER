"""Full AD-6 lease/fencing claim mechanics for `candidate_jobs` (Story
4.1), mirroring `preparation_claim.py`'s claim/heartbeat shape against this
table's actual columns.

Story 4.2 adds `stage_parse_success`/`stage_parse_failure` -- but "success"
here means only "a parse artifact was persisted", not full Candidate-job
completion (AD-8 also requires a provider proposal, which does not exist
until Stories 4.3-4.4). A successful parse advances `candidate_jobs.status`
to `'parsed'`, a value deliberately outside both `claim_queued`'s `WHERE
status = 'queued'` and `sweep_stale`'s `WHERE status = 'claimed'` -- a
`'parsed'` job is correctly invisible to both until a later story extends
the claim/sweep vocabulary to resume it for the provider phase. Nothing in
production calls `claim_queued` yet (see Story 4.1's Dev Notes and this
story's own Scope reality check for why wiring an idle poll loop here would
still be premature).
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass

from psycopg import Connection

from .preparation_claim import LEASE_SECONDS, MAX_ATTEMPTS


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


def stage_parse_success(
    conn: Connection,
    job_id: str,
    generation: int,
    token: str,
    *,
    candidate_id: str,
    document_id: str,
    document_content_version: int,
    parser_pipeline_version: str,
    source_units_json: list,
    blocks_json: list,
    gate_codes: list[str],
    coherent_block_count: int,
    display_name: str,
    name_source: str,
    email: str | None,
    phone: str | None,
) -> bool:
    """CAS: only succeeds if this job is still the one we claimed, fenced on
    generation+token+active lease exactly like `preparation_claim.stage_success`
    (AC#3). Persists the parse artifact and display identity and advances
    `status` to `'parsed'`, all in one transaction -- or none of it, if the
    fence has already been lost.

    The `parse_artifacts` insert is `ON CONFLICT DO NOTHING` on
    `(document_id, document_content_version, parser_pipeline_version)`
    (AC#3's idempotent duplicate delivery -- a second fenced write for the
    same Document version changes nothing, it does not error). The
    `candidate_identities` insert is `ON CONFLICT (candidate_id) DO UPDATE`
    (a re-parse, e.g. attempt 2, refreshes the display identity rather than
    duplicating or erroring)."""
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE candidate_jobs
            SET status = 'parsed', state_version = state_version + 1
            WHERE id = %s AND generation = %s AND lease_token = %s
              AND status = 'claimed' AND lease_expires_at > now()
            """,
            (job_id, generation, token),
        )
        if cur.rowcount != 1:
            return False
        conn.execute(
            """
            INSERT INTO parse_artifacts (
                id, candidate_id, document_id, document_content_version,
                parser_pipeline_version, source_units_json, blocks_json,
                gate_codes, coherent_block_count, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (document_id, document_content_version, parser_pipeline_version)
            DO NOTHING
            """,
            (
                str(uuid.uuid4()),
                candidate_id,
                document_id,
                document_content_version,
                parser_pipeline_version,
                json.dumps(source_units_json),
                json.dumps(blocks_json),
                json.dumps(gate_codes),
                coherent_block_count,
            ),
        )
        conn.execute(
            """
            INSERT INTO candidate_identities (id, candidate_id, display_name, name_source, email, phone, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (candidate_id) DO UPDATE
            -- A re-parse (e.g. attempt 2) refreshes the identity, but never
            -- regresses a genuinely parsed name back to the Document
            -- Reference/filename fallback, and never nulls out a
            -- previously-found email/phone with an absent one (review
            -- finding: an unguarded overwrite would do both).
            SET display_name = CASE
                    WHEN EXCLUDED.name_source = 'parsed' OR candidate_identities.name_source = 'fallback'
                    THEN EXCLUDED.display_name ELSE candidate_identities.display_name END,
                name_source = CASE
                    WHEN EXCLUDED.name_source = 'parsed' OR candidate_identities.name_source = 'fallback'
                    THEN EXCLUDED.name_source ELSE candidate_identities.name_source END,
                email = COALESCE(EXCLUDED.email, candidate_identities.email),
                phone = COALESCE(EXCLUDED.phone, candidate_identities.phone)
            """,
            (str(uuid.uuid4()), candidate_id, display_name[:255], name_source, email, phone),
        )
        return True


def stage_parse_failure(
    conn: Connection,
    job_id: str,
    attempt: int,
    generation: int,
    token: str,
    failure_reason: str,
) -> bool:
    """CAS: requeue for another attempt if attempts remain, else terminate
    `'failed'`. Fenced on generation+token+active lease like
    `stage_parse_success` (AC#3). No `parse_artifacts`/`candidate_identities`
    row is ever written here (AC#1: a fatal parse produces no artifact, not
    an artifact full of nulls)."""
    reason = failure_reason[:64]
    with conn.transaction():
        if attempt < MAX_ATTEMPTS:
            cur = conn.execute(
                """
                UPDATE candidate_jobs
                SET status = 'queued', attempt = attempt + 1, failure_reason = %s,
                    state_version = state_version + 1,
                    lease_token = NULL, lease_expires_at = NULL
                WHERE id = %s AND generation = %s AND lease_token = %s
                  AND status = 'claimed' AND lease_expires_at > now()
                """,
                (reason, job_id, generation, token),
            )
        else:
            cur = conn.execute(
                """
                UPDATE candidate_jobs
                SET status = 'failed', failure_reason = %s, state_version = state_version + 1
                WHERE id = %s AND generation = %s AND lease_token = %s
                  AND status = 'claimed' AND lease_expires_at > now()
                """,
                (reason, job_id, generation, token),
            )
        return cur.rowcount == 1
