"""Pure retry-in-new-revision eligibility rule (Story 5.3, AD-12) — no
DB/network/framework import. Enforced by tests/test_domain_boundary.py.

CLAUDE.md's architecture invariants name "retry" explicitly alongside
scoring/Evidence/publication as logic that must stay framework-free — this
is the single place the eligibility rule lives, so the adapter never
re-derives it inline.
"""

from __future__ import annotations

_ELIGIBLE_SESSION_STATUS = "frozen_inputs"
_ELIGIBLE_MEMBERSHIP_OUTCOME = "Failed"


def check_retry_eligibility(session_status: str, membership_outcome: str, allowance_consumed: bool) -> bool:
    """A `Retry in new revision` is eligible only when the owning Analysis
    Session is still in its one working status, the Candidate's membership on
    the current published revision is exactly `"Failed"` (AD-12: "Already-
    scored and deterministic Needs Review outcomes are not manually
    retryable" — `NeedsReview`, `NewResult`, and `ReusedResult` are all
    ineligible), and the Document's one-per-Document allowance has not
    already been consumed."""
    return (
        session_status == _ELIGIBLE_SESSION_STATUS
        and membership_outcome == _ELIGIBLE_MEMBERSHIP_OUTCOME
        and not allowance_consumed
    )
