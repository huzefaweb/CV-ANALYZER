"""Pure print-eligibility/scope/trigger derivation (Story 7.3, AC#1-3, AR-34,
AR-43). No DB/network/framework imports. Enforced by
tests/test_domain_boundary.py's existing `src/domain/*.py` glob.

Sibling to `question_set_projection.py`: this module does not recompute the
six-value Question Set vocabulary, it only branches on the already-derived
state to decide print scope/blocking (AD-14/AD-18).
"""

from __future__ import annotations

SCORED_COMBINED = "ScoredCombined"
REPORT_ONLY = "ReportOnly"

PRINT_SCOPES = (SCORED_COMBINED, REPORT_ONLY)

# Mirrors question_set_projection.py's COMPLETE constant value without
# importing it — that module lives one layer below report-outcome framing
# and this module only needs the one frozen string, not its full vocabulary.
_QUESTION_SET_COMPLETE = "Complete"


def derive_print_scope(outcome: str) -> str:
    """`"Ranked"` (candidate_report's own collapsed NewResult/ReusedResult
    label) prints the full scored-combined scope; `"NeedsReview"` prints
    report-only. Any other outcome is unreachable for an already-authorized
    report (Failed has no Candidate Report at all — AC#3), defended rather
    than silently mishandled."""
    if outcome == "Ranked":
        return SCORED_COMBINED
    if outcome == "NeedsReview":
        return REPORT_ONLY
    raise ValueError(f"unrecognized report outcome={outcome!r}")


def is_print_blocked(outcome: str, question_set_state: str) -> bool:
    """AC#2: only a scored (`"Ranked"`) Candidate can be blocked, and only
    when its current Question Set isn't `Complete` yet. Needs Review has no
    Question Set dependency at all (AD-18) and is never blocked."""
    if outcome != "Ranked":
        return False
    return question_set_state != _QUESTION_SET_COMPLETE


def derive_trigger(revision_number: int, retried_document_reference: str | None) -> str:
    """Matches EXPERIENCE.md's Print Contract literal wording
    (`Trigger: Retry of Document A7K2`). `revision_number == 1` is always
    the session's initial analysis (Story 3.5); every later revision is
    created by exactly one Document's retry allowance (AD-12) — the caller
    looks up that Document's reference and passes it here.
    `retried_document_reference is None` for `revision_number > 1` is
    unreachable under AD-12 but defended rather than raising, since a print
    projection must never crash on a data-integrity edge case."""
    if revision_number == 1:
        return "Initial analysis"
    if retried_document_reference is not None:
        return f"Retry of Document {retried_document_reference}"
    return "Retry"
