"""Gateway-side Question Set row-state derivation (Story 7.2, AR-34). Pure,
no DB/network/framework imports. Enforced by tests/test_domain_boundary.py.

Mirrors `progress_projection.py`'s exact shape and reasoning: translates the
internal `question_set_jobs` bookkeeping fields into one of six frozen,
Recruiter-safe public states.

`question_set_jobs.status` is not itself monotonic (a worker-driven retry or
a sweep-driven reclaim resets it from `'claimed'` back to `'queued'`), but
this function's output is: a reset that would otherwise look like
"Generating -> Generating" (still true, no user-visible regression) is
distinguished into `"Recovering"`/`"Retrying"` the same way
`progress_projection.derive_row_state` already does for `candidate_jobs`,
by consulting `reclaim_count`/`failure_reason` on the `'queued'` branch.

`question_finalizer.py` (Story 7.2's gateway Question coordinator) is the
only writer that ever advances `status` to `'published'`; a `'failed'` job
was exhausted by the worker's own `MAX_ATTEMPTS` budget (Story 7.1) or
rejected by the coordinator's own completeness gate
(`question_set_completeness.py`) — both collapse to the same public
`"Failed"` state, matching AC#3's "Failed/exhausted is stable and
sanitized" (a Recruiter never needs to distinguish the two to act — either
way, `"Retrying"` via this story's isolated retry command is the only next
step).
"""

from __future__ import annotations

NOT_GENERATED = "NotGenerated"
GENERATING = "Generating"
RECOVERING = "Recovering"
RETRYING = "Retrying"
COMPLETE = "Complete"
FAILED = "Failed"

QUESTION_SET_STATES = (NOT_GENERATED, GENERATING, RECOVERING, RETRYING, COMPLETE, FAILED)


def derive_question_set_state(
    job_status: str | None,
    reclaim_count: int,
    failure_reason: str | None,
) -> str:
    """Derives one of the six frozen public states for one Candidate's
    Question Set. `job_status is None` means no `question_set_jobs` row
    exists yet at all (generation never requested)."""
    if job_status is None:
        return NOT_GENERATED
    if job_status == "published":
        return COMPLETE
    if job_status in ("failed", "unrecoverable"):
        # 'unrecoverable' (question_finalizer.py::_unstick_job) is a
        # data-integrity edge case distinct from a genuine worker/coordinator
        # failure, but both collapse to the same public FAILED state — a
        # Recruiter never needs to distinguish them to act.
        return FAILED
    if job_status in ("claimed", "completed"):
        return GENERATING
    if job_status == "queued":
        if failure_reason is not None:
            return RETRYING
        if reclaim_count > 0:
            return RECOVERING
        return GENERATING
    raise ValueError(f"unrecognized question_set_jobs.status={job_status!r}")
