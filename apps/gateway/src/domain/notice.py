"""Server-owned, versioned responsible-hiring notice (AR-46) — pure, no
DB/network/framework imports. Enforced by tests/test_domain_boundary.py.

One standalone constant module, not inlined into `workspace.py`'s Results
endpoint (its first caller, Story 5.2): AR-46 requires the exact same
projection "used unchanged by Ranked Results, Candidate Report, scored
print, and Needs Review print" — Epic 6/7 stories import this same module
later. Clients render the complete projection and never reconstruct
optional fragments (AR-46) — always send/read the whole dict, never a
partial field subset.
"""

from __future__ import annotations

NOTICE_VERSION = 1

RESPONSIBLE_HIRING_NOTICE: dict[str, object] = {
    "version": NOTICE_VERSION,
    "text": (
        "AI-generated analysis may contain errors; it is advisory. Scores summarize "
        "Resume Evidence against the Job Description; they do not recommend "
        "suitability, rejection, or hiring. Human verification is required."
    ),
}
"""Carries all five AR-46 meanings (EXPERIENCE.md line 103's "Required
ranking/report/print meaning"): AI may err; analysis is advisory; scores
summarize Resume Evidence against the Job Description; scores do not
recommend suitability, rejection, or hiring; human verification is
required."""
