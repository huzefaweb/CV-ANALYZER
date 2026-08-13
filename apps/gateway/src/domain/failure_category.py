"""Gateway-side failure-category normalization (Story 4.6, AR-40) — pure,
no DB/network/framework imports. Enforced by tests/test_domain_boundary.py.

`candidate_jobs.failure_reason` reaches this module in three different
shapes, traced directly against apps/worker/src/main.py and
apps/gateway/src/adapters/recovery_sweep.py:

- Provider-phase failures (`stage_provider_failure`) are staged with
  `AnalysisProviderError.category`, which is `map_failure(reason)`
  (apps/worker/src/domain/analysis_provider.py) — already one of the five
  frozen public categories.
- Parse-phase failures (`stage_parse_failure`) are staged with `str(exc)`
  (a `ParseFatalError` message) or the literal `"unexpected processing
  error"` — neither is a frozen category.
- Sweep-driven lease exhaustion (recovery_sweep.py) stages the literal
  `"lease_exhausted"` — also not a frozen category.

This function normalizes all three shapes to exactly one of AR-40's five
frozen public categories.
"""

from __future__ import annotations

_FROZEN_CATEGORIES = frozenset(
    {
        "Analysis timed out",
        "Analysis service unavailable",
        "Analysis response could not be validated",
        "Automated analysis unavailable for this document",
        "Document processing interrupted",
    }
)


def map_failure_reason_to_category(failure_reason: str | None) -> str:
    """Normalizes `candidate_jobs.failure_reason` to one of AR-40's five
    frozen public categories. `"lease_exhausted"` (a stalled/crashed worker
    mid-processing) maps to `"Document processing interrupted"`. Any other
    unrecognized value (raw parse-exception text, `None`) maps to
    `"Automated analysis unavailable for this document"` — the
    per-document-scoped category, the closest fit for a parse-layer
    failure specific to one Document. Both are disclosed judgment calls,
    not spec-given mappings — epics.md/AR-40 name the five categories but
    do not map these non-frozen inputs to any of them."""
    if failure_reason in _FROZEN_CATEGORIES:
        return failure_reason
    if failure_reason == "lease_exhausted":
        return "Document processing interrupted"
    return "Automated analysis unavailable for this document"
