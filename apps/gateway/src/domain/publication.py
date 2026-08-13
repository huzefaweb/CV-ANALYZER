"""Pure ranking-assignment kernel (Story 5.1, AD-10, AR-30) — no DB/network
import. Enforced by tests/test_domain_boundary.py.

Consumes `apps/gateway/src/domain/scoring.py::rank_order`'s already-sorted,
already-tie-broken output (Story 4.5, unmodified) and assigns the three
persisted fields AD-10 requires: `rank_position` (standard competition
ranking — ties share the same position, the next distinct score's position
accounts for every entry ranked above it, e.g. scores [10, 10, 5] ->
positions [1, 1, 3], never a dense [1, 1, 2] renumbering), `tie_group`
(computed identically to `rank_position` in V1 — AD-10 defines exactly one
tie condition, equal exact overall score, so there is one grouping concept,
not two independent ones; kept as a separate field only because AD-10
explicitly instructs persisting rank position and tie group separately, a
schema-level distinction, not a value-level one), and `presentation_ordinal`
(strictly sequential 1..N over `rank_order`'s fully tie-broken order — the
frozen non-hiring secondary presentation order).
"""

from __future__ import annotations

from typing import Sequence

from .scoring import CandidateScore


def assign_ranks(ordered_scores: Sequence[CandidateScore]) -> list[tuple[str, int, int, int]]:
    """Returns `(candidate_key, rank_position, tie_group, presentation_ordinal)`
    per entry, in the same order as `ordered_scores` (which must already be
    `rank_order`'s output — this function does not sort)."""
    result: list[tuple[str, int, int, int]] = []
    current_rank = 0
    previous_score = None
    for index, candidate in enumerate(ordered_scores):
        presentation_ordinal = index + 1
        if previous_score is None or candidate.overall_score_bps != previous_score:
            current_rank = presentation_ordinal
        result.append((candidate.candidate_key, current_rank, current_rank, presentation_ordinal))
        previous_score = candidate.overall_score_bps
    return result
