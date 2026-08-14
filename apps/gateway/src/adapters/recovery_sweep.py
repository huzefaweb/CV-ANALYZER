"""Gateway recovery-sweep coordinator (Story 4.1, AD-6, AR-15, AR-18):
a stateless, level-triggered scan that reclaims or exhausts stale leases on
`start_preparations` and `candidate_jobs` uniformly. No persistent claim —
a short transaction-scoped `FOR UPDATE SKIP LOCKED` claim per stale row is
the whole reauthorization mechanism, mirroring `preparation_finalizer.py`.

Correction from the Story 1.5a `job_lease.py` proof harness: that harness's
`sweep_stale` rotates `generation`/`lease_token` on reclaim but never resets
`status` away from `'claimed'`, which would make a reclaimed row
permanently unclaimable (`claim_queued` only ever selects the queued
status). This production sweep resets status back to the table's queued
value on reclaim, so the row is genuinely claimable again — matching
MODELS.md's `Recovering1 --> Attempt1Processing` transition.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

MAX_RECLAIMS_PER_ATTEMPT = 1
# Mirrors apps/worker/src/adapters/preparation_claim.py::MAX_ATTEMPTS — a
# cross-package duplication of a single constant (worker and gateway are
# separate deployables with no shared package, the same "port, not import"
# boundary Story 3.5 established for scoring_configuration.py). Both sides
# implement the same AR-16 "attempt 1 plus at most attempt 2" budget
# independently; a change to one must be mirrored in the other.
MAX_ATTEMPTS = 2

# (table, processing-status value, queued-status value)
_LEASED_TABLES: tuple[tuple[str, str, str], ...] = (
    ("start_preparations", "deriving", "queued"),
    ("candidate_jobs", "claimed", "queued"),
    # Story 4.4: `'parsed'` is the mid-attempt checkpoint after a successful
    # parse but before provider-phase staging -- the lease stays held
    # (candidate_claim.stage_parse_success never clears it), so a worker
    # crash here must be reclaimable the same way a `'claimed'` row is. A
    # full re-attempt (back to `'queued'`) simply redoes the idempotent
    # parse (parse_artifacts' ON CONFLICT DO NOTHING) before retrying the
    # provider call -- no separate resume path is needed.
    ("candidate_jobs", "parsed", "queued"),
    # Story 7.1 code review (Blind Hunter/Edge Case Hunter/Acceptance
    # Auditor convergent finding): question_set_jobs carries the identical
    # AD-6 lease/fencing/reclaim_count columns as candidate_jobs but was
    # never added here, so a worker crash mid-attempt left the row
    # permanently `claimed` with no recovery path — a direct AR-15/AR-18
    # violation ("no persistent claim may become stale").
    ("question_set_jobs", "claimed", "queued"),
)


def _sweep_table(db: OrmSession, table: str, processing_status: str, queued_status: str) -> int:
    # ponytail: no LIMIT/no supporting index on (status, lease_expires_at) —
    # V1 single-demo-host scale (a handful of in-flight jobs at once); add
    # an index and a batch LIMIT if a future scale-up makes a full-table
    # scan measurably slow.
    stale = db.execute(
        text(
            f"""
            SELECT id, attempt, reclaim_count
            FROM {table}
            WHERE status = :processing_status
              AND (lease_expires_at IS NULL OR lease_expires_at <= now())
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"processing_status": processing_status},
    ).fetchall()

    touched = 0
    for row_id, attempt, reclaim_count in stale:
        # Every branch reasserts `status = :processing_status` even though
        # the SELECT above already holds a row lock — defense in depth,
        # matching preparation_finalizer.py's established CAS discipline
        # rather than relying on the lock alone.
        if reclaim_count < MAX_RECLAIMS_PER_ATTEMPT:
            db.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET status = :queued_status, generation = generation + 1,
                        reclaim_count = reclaim_count + 1,
                        lease_token = NULL, lease_expires_at = NULL
                    WHERE id = :id AND status = :processing_status
                    """
                ),
                {"queued_status": queued_status, "processing_status": processing_status, "id": row_id},
            )
        elif attempt < MAX_ATTEMPTS:
            db.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET status = :queued_status, attempt = attempt + 1, reclaim_count = 0,
                        lease_token = NULL, lease_expires_at = NULL,
                        failure_reason = 'lease_exhausted'
                    WHERE id = :id AND status = :processing_status
                    """
                ),
                {"queued_status": queued_status, "processing_status": processing_status, "id": row_id},
            )
        else:
            db.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET status = 'failed', failure_reason = 'lease_exhausted',
                        lease_token = NULL, lease_expires_at = NULL
                    WHERE id = :id AND status = :processing_status
                    """
                ),
                {"processing_status": processing_status, "id": row_id},
            )
            if table == "start_preparations":
                _unlock_session_for_preparation(db, row_id)
        touched += 1
    return touched


def _unlock_session_for_preparation(db: OrmSession, preparation_id: str) -> None:
    """AC#2: inputs unlock only when the preparation reaches a terminal
    state — sweep-driven lease exhaustion is one such terminus, the same
    rule `preparation_claim.stage_failure` already applies on the
    worker-terminated path."""
    session_id = db.execute(
        text("SELECT analysis_session_id FROM start_preparations WHERE id = :id"), {"id": preparation_id}
    ).scalar_one()
    db.execute(
        text(
            "UPDATE analysis_sessions SET status = 'draft' "
            "WHERE id = :session_id AND status = 'preparing_to_start'"
        ),
        {"session_id": session_id},
    )


def sweep_stale(db: OrmSession) -> int:
    """Reclaims or exhausts every stale-leased row across both lease-bearing
    tables. Returns the total number of rows touched. Safe to call
    repeatedly (immediate startup scan, ≤2-second periodic scan).

    Commits once per table rather than once for the whole call — an
    unexpected failure partway through one table's rows must not roll back
    the other table's already-applied reclaims (each table's rows are
    otherwise unrelated, so there is no atomicity requirement spanning
    them)."""
    touched = 0
    for table, processing_status, queued_status in _LEASED_TABLES:
        touched += _sweep_table(db, table, processing_status, queued_status)
        db.commit()
    return touched
