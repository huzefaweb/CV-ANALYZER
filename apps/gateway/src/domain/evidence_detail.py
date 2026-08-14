"""Pure full-Evidence-row selection kernel (Story 6.2, AC#1, NFR-1, NFR-24,
AR-25) — no DB/network/framework imports. Enforced by tests/test_domain_boundary.py.

Sibling to `evidence_summary.py`, not an extension of it: that module
selects a capped, display-length subset (2-4 strengths, 1-3 gaps) for
Ranked Results/Candidate Report's summary regions. This module returns
*every* proposal item as one `EvidenceRow`, including its locator/excerpt,
for the dedicated Evidence inspection section Story 5.2's Dev Notes
deferred to "Story 6.2's future scope."

`unit_locators` is the plain JSON-dict shape `apps/worker/src/adapters/
source_unit_serialization.py::to_json` already persists into
`parse_artifacts.source_units_json` — this module reads that dict shape
directly rather than importing `apps/worker`'s dataclasses, since the two
apps share no package (see `scoring_configuration.py`'s identical note).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .scoring import criterion_state_from_label
from .scoring_configuration import Component

_NOT_FOUND_STATE = "Not Found"


@dataclass(frozen=True)
class EvidenceRow:
    """One Job Requirement's full Evidence conclusion — never capped,
    never fabricated. A Not Found row always carries `locator_description
    = None` and `excerpt = ""`, even if the raw proposal item happened to
    carry a populated locator/excerpt (AC#1: "fabricated locators are
    absent")."""

    requirement_display_id: str
    requirement_text: str
    state: str
    locator_description: str | None
    excerpt: str


def _component_order_index(component: Component) -> int:
    return list(Component).index(component)


def _describe_locator(locator: Mapping[str, object] | None) -> str | None:
    """AR-25: PDF locates by page, DOCX by stable section/paragraph/table
    path — both already carry the fields needed, no computation. An
    unrecognized/malformed locator shape is defended, not expected: it
    never raises, it simply cannot be described (`None`)."""
    if locator is None:
        return None
    locator_type = locator.get("type")
    if locator_type == "pdf":
        return f"Page {locator['page']}"
    if locator_type == "docx":
        return str(locator["path"])
    return None


def build_evidence_rows(
    proposal_items: Sequence[Mapping[str, object]],
    requirement_texts: Mapping[str, str],
    requirement_display_ids: Mapping[str, str],
    requirement_components: Mapping[str, Component],
    unit_locators: Mapping[str, Mapping[str, object] | None],
) -> list[EvidenceRow]:
    """Every `proposal_items` entry becomes one `EvidenceRow`, ordered by
    the frozen AD-10 component order then `requirement_display_id` — the
    same determinism rule `evidence_summary.py::summarize_evidence` uses,
    applied here with no cap."""

    # criterion_state_from_label validates every state label is one of the
    # four frozen wire values, same defensive-validation reuse as
    # evidence_summary.py.
    for item in proposal_items:
        criterion_state_from_label(str(item["state"]))

    ordered = sorted(
        proposal_items,
        key=lambda item: (
            _component_order_index(requirement_components[str(item["job_requirement_id"])]),
            str(item["job_requirement_id"]),
        ),
    )

    rows: list[EvidenceRow] = []
    for item in ordered:
        requirement_id = str(item["job_requirement_id"])
        state = str(item["state"])
        if state == _NOT_FOUND_STATE:
            locator_description: str | None = None
            excerpt = ""
        else:
            unit_id = item.get("locator")
            locator = unit_locators.get(str(unit_id)) if unit_id else None
            locator_description = _describe_locator(locator)
            excerpt = str(item.get("excerpt") or "")
        rows.append(
            EvidenceRow(
                requirement_display_id=requirement_display_ids[requirement_id],
                requirement_text=requirement_texts[requirement_id],
                state=state,
                locator_description=locator_description,
                excerpt=excerpt,
            )
        )
    return rows
