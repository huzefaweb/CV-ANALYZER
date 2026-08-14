"""Pure minimized grounded-context builder for Interview Question Set
generation (Story 7.1; AR-21, AR-22, AR-40, NFR-1, NFR-9).

Deliberately the worker's own copy of `apps/gateway/src/domain/
evidence_detail.py::EvidenceRow`/`build_evidence_rows`'s shape (minus
`requirement_display_id`, a gateway display concern only) — the two apps
share no package (see `evidence_detail.py`'s and `scoring_configuration.py`'s
identical existing notes on this), so this is the established duplication
convention in this codebase, not a new one.

No DB/network/framework imports here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .analysis_provider import AnalysisState

_NOT_FOUND_STATE = "Not Found"


@dataclass(frozen=True)
class GroundedRequirement:
    """One Job Requirement's minimized, provider-safe grounding context —
    never fabricated, never carrying identity/protected/proxy content. A Not
    Found row always carries `locator_description = None` and `excerpt =
    ""`, even if the raw proposal item happened to carry a populated
    locator/excerpt (mirrors evidence_detail.py's identical AC#1 rule)."""

    job_requirement_id: str
    requirement_text: str
    state: str
    locator_description: str | None
    excerpt: str


def _describe_locator(locator: Mapping[str, object] | None) -> str | None:
    """Mirrors evidence_detail.py::_describe_locator's intent. An
    unrecognized/malformed locator shape is defended, not expected: it never
    raises, it simply cannot be described (`None`). Code review fix (Edge
    Case Hunter): the original used `locator['page']`/`locator['path']` hard
    subscripts, contradicting this exact docstring's "never raises" claim
    for a `{"type": "pdf"}` locator missing its `page` key — `.get(...)`
    instead of `[...]` on the field access closes that gap without changing
    the `type` dispatch itself."""
    if locator is None:
        return None
    locator_type = locator.get("type")
    if locator_type == "pdf" and locator.get("page") is not None:
        return f"Page {locator['page']}"
    if locator_type == "docx" and locator.get("path") is not None:
        return str(locator["path"])
    return None


def build_grounded_context(
    proposal_items: Sequence[Mapping[str, object]],
    requirement_texts: Mapping[str, str],
    unit_locators: Mapping[str, Mapping[str, object] | None],
) -> list[GroundedRequirement]:
    """Every `proposal_items` entry (the Candidate's already-persisted
    Evidence conclusions) becomes one `GroundedRequirement`. No `Component`/
    AD-10 ordering logic here — the provider doesn't need component order,
    unlike `evidence_detail.py`'s display-ordered rows."""
    rows: list[GroundedRequirement] = []
    for item in proposal_items:
        requirement_id = str(item["job_requirement_id"])
        state = str(item["state"])
        # Code review fix (Blind Hunter/Acceptance Auditor, High): mirrors
        # evidence_detail.py::build_evidence_rows's own hard-subscript/
        # validated-state discipline exactly, which this module's docstring
        # already claimed to mirror but didn't. AnalysisState(state) raises
        # ValueError on an unrecognized label (was silently passed through
        # verbatim into the provider prompt); requirement_texts[...] raises
        # KeyError on a stale/unknown requirement id (was silently
        # `.get(..., "")`, producing an empty-text grounding line with no
        # signal anything was wrong). Both are now loud, not swallowed —
        # main.py's caller wraps this call and stages a sanitized failure
        # against the attempt budget instead of fabricating from a gap.
        AnalysisState(state)
        requirement_text = requirement_texts[requirement_id]
        if state == _NOT_FOUND_STATE:
            locator_description: str | None = None
            excerpt = ""
        else:
            unit_id = item.get("locator")
            locator = unit_locators.get(str(unit_id)) if unit_id else None
            locator_description = _describe_locator(locator)
            excerpt = str(item.get("excerpt") or "")
        rows.append(
            GroundedRequirement(
                job_requirement_id=requirement_id,
                requirement_text=requirement_text,
                state=state,
                locator_description=locator_description,
                excerpt=excerpt,
            )
        )
    return rows
