"""Gateway-side Progress row-state derivation (Story 4.7, AR-34) — pure,
no DB/network/framework imports. Enforced by tests/test_domain_boundary.py.

Translates the internal `candidate_jobs`/`revision_memberships` bookkeeping
fields into one of eight frozen, Recruiter-safe public states. Traced
directly against the exact field mutations already proven in
`apps/worker/src/adapters/candidate_claim.py` (`stage_parse_failure`/
`stage_provider_failure`) and `apps/gateway/src/adapters/recovery_sweep.py`
(`_sweep_table`):

- A worker-driven retryable failure (before attempt exhaustion) requeues to
  `status='queued', attempt+=1, failure_reason=<set>, reclaim_count=0`.
- A sweep-driven mid-attempt reclaim requeues to `status='queued',
  reclaim_count+=1` — `failure_reason` is left untouched (no failure has
  occurred yet, just a stalled lease).
- A sweep-driven reclaim-exhaustion (attempt still has budget left) requeues
  to `status='queued', attempt+=1, reclaim_count=0,
  failure_reason='lease_exhausted'` — the same shape as a worker-driven
  retry.
- Attempt exhaustion (either path) terminates at `status='failed'`.
- Story 4.6's finalizer coordinator is the only writer that ever advances
  `status` to `'finalized'` and `revision_memberships.outcome` away from
  `'queued'`.

The raw `candidate_jobs.status` column is *not* itself monotonic — a
reclaim resets it from `'claimed'`/`'parsed'` back to `'queued'` — but this
function's output is: a reset that would otherwise look like `"Analyzing"
-> "Queued"` is never displayed as bare `"Queued"`, because the same reset
always carries either `reclaim_count > 0` or `failure_reason is not None`,
which route to `"Recovering"`/`"Retrying - Attempt 2 of 2"` instead. A
client that has ever seen `"Parsing"`/`"Analyzing"`/`"Recovering"`/
`"Retrying..."` never afterward sees `"Queued"` for the same row, by
construction of `candidate_claim.py`'s and `recovery_sweep.py`'s own field
mutations (attempt/reclaim_count only ever increase before a terminal
state).
"""

from __future__ import annotations

# The frozen, Recruiter-safe public vocabulary this function ever returns.
# "Needs Review"/"Failed" reuse NFR-24's exact terminology; "Recovering"/
# "Retrying - Attempt 2 of 2" reuse Story 4.6's own established before-
# exhaustion UI copy verbatim (see that story's Implementation Contract).
QUEUED = "Queued"
PARSING = "Parsing"
ANALYZING = "Analyzing"
RECOVERING = "Recovering"
RETRYING = "Retrying - Attempt 2 of 2"
NEEDS_REVIEW = "Needs Review"
SUCCEEDED = "Succeeded"
FAILED = "Failed"

ROW_STATES = (QUEUED, PARSING, ANALYZING, RECOVERING, RETRYING, NEEDS_REVIEW, SUCCEEDED, FAILED)

_TERMINAL_OUTCOME_TO_STATE = {
    "NewResult": SUCCEEDED,
    "ReusedResult": SUCCEEDED,
    "NeedsReview": NEEDS_REVIEW,
    "Failed": FAILED,
}


def derive_row_state(
    job_status: str,
    reclaim_count: int,
    failure_reason: str | None,
    membership_outcome: str,
) -> str:
    """Derives one of the eight frozen public states for one Candidate row.

    `reclaim_count` is only consulted on the `'queued'` branch — it is
    irrelevant once a job has moved past `'queued'` for the current cycle
    (`'claimed'`/`'parsed'`/`'completed'`/`'failed'`/`'finalized'` already
    carry their own unambiguous state). `RETRYING`'s label is a literal, not
    computed from `attempt` — every requeue-with-`failure_reason` path
    (worker retry, sweep reclaim-exhaustion) bumps `attempt` to 2 in the
    same statement, so `failure_reason is not None` on a `'queued'` row is
    sufficient on its own; this function does not re-verify `attempt`
    against `MAX_ATTEMPTS` (a value it would otherwise have to duplicate
    from `apps/worker/src/adapters/preparation_claim.py`/
    `apps/gateway/src/adapters/recovery_sweep.py` with no shared constant)."""
    if job_status == "finalized":
        state = _TERMINAL_OUTCOME_TO_STATE.get(membership_outcome)
        if state is None:
            raise ValueError(
                f"finalized job with non-terminal membership_outcome={membership_outcome!r} — "
                "Story 4.6's finalizer only ever CASes revision_memberships.outcome to "
                "NewResult/NeedsReview/Failed in the same transaction it sets status='finalized' "
                "(ReusedResult is Story 5.3's retry-revision carried-forward outcome, also finalized)"
            )
        return state
    if job_status == "failed":
        return FAILED
    if job_status == "claimed":
        return PARSING
    if job_status in ("parsed", "completed"):
        return ANALYZING
    if job_status == "queued":
        if failure_reason is not None:
            return RETRYING
        if reclaim_count > 0:
            return RECOVERING
        return QUEUED
    raise ValueError(f"unrecognized candidate_jobs.status={job_status!r}")
