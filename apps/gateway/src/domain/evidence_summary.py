"""Pure Evidence-summary selection kernel (Story 5.2, AC#1, NFR-1, NFR-24) —
no DB/network/framework imports. Enforced by tests/test_domain_boundary.py.

`candidate_results` (Story 4.6) persists only scores/gate_codes/failure
fields — no strengths/gaps text column exists anywhere in the schema. The
per-requirement Matched/Partial/Not-Found/Needs-Validation classification
lives only in `candidate_proposals.items_json` (Story 4.4's immutable
provider proposal, shaped like `apps/worker/src/domain/
analysis_provider.ProposalItem`: `job_requirement_id`/`state`/`locator`/
`excerpt`). This module selects a small, deterministic, display-ready subset
of that data for one Candidate's Ranked Results row — it does not touch the
raw excerpt (too long for a compact row; full Evidence inspection is
Candidate Report's future scope, Story 6.1) and it never fabricates content
for a Job Requirement with no corresponding proposal item.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .scoring import criterion_state_from_label
from .scoring_configuration import Component

_STRENGTH_STATES = ("Matched", "Partial")
_GAP_STATES = ("Not Found", "Needs Validation")

_MAX_STRENGTHS = 4
_MAX_GAPS = 3


@dataclass(frozen=True)
class EvidencePoint:
    """One strength or gap, phrased against the Job Requirement it resolves
    to (NFR-1) — never a bare score or unlinked claim."""

    requirement_text: str
    state: str
    """One of the four frozen wire labels ("Matched", "Partial", "Not
    Found", "Needs Validation") — lets the caller phrase Not Found
    ("Evidence not found for X") distinctly from Needs Validation
    ("Needs Validation: X"), preserving NFR-24's required distinction."""


@dataclass(frozen=True)
class EvidenceSummary:
    strengths: list[EvidencePoint]
    gaps: list[EvidencePoint]


def _component_order_index(component: Component) -> int:
    return list(Component).index(component)


def summarize_evidence(
    proposal_items: Sequence[Mapping[str, object]],
    requirement_texts: Mapping[str, str],
    requirement_components: Mapping[str, Component],
) -> EvidenceSummary:
    """Selects up to 4 strengths (Matched/Partial) and up to 3 gaps (Not
    Found/Needs Validation) from `proposal_items` (the shape persisted in
    `candidate_proposals.items_json`: each item has `job_requirement_id`
    and `state`), ordered by the frozen AD-10 component order and then by
    `job_requirement_id` for full determinism (never dict/JSON key order).
    Returns fewer than the cap when fewer items qualify — "2-4"/"1-3" is a
    display floor a full corpus fixture should hit, not a hard invariant
    this function forces by padding or fabricating entries."""

    def _ordered(states: tuple[str, ...], cap: int) -> list[EvidencePoint]:
        matches = [item for item in proposal_items if item.get("state") in states]
        matches.sort(
            key=lambda item: (
                _component_order_index(requirement_components[str(item["job_requirement_id"])]),
                str(item["job_requirement_id"]),
            )
        )
        points = [
            EvidencePoint(
                requirement_text=requirement_texts[str(item["job_requirement_id"])],
                state=str(item["state"]),
            )
            for item in matches[:cap]
        ]
        return points

    # criterion_state_from_label validates every state label is one of the
    # four frozen wire values (raises on anything else) — called for its
    # validation side effect even though its return value isn't needed here,
    # so a corrupt/unrecognized state fails loudly rather than silently
    # sorting into neither bucket.
    for item in proposal_items:
        criterion_state_from_label(str(item["state"]))

    return EvidenceSummary(
        strengths=_ordered(_STRENGTH_STATES, _MAX_STRENGTHS),
        gaps=_ordered(_GAP_STATES, _MAX_GAPS),
    )
