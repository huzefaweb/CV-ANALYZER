"""Job Requirement canonicalization and duplicate merge/conflict rules
(AD-4, AR-10, SOLUTION-DESIGN.md §5) — pure, no DB/web/SDK imports.

Canonicalization is Unicode NFKC + case fold + whitespace collapse +
surrounding-punctuation removal, applied in Job Description source order.
Canonically identical statements with the same component collapse into one
requirement, retaining every verified source locator, in first-source
order. A canonical duplicate whose classification (mandatory/preferred)
differs is a conflict, not a silent merge — V1 code does not guess which
classification wins. Non-identical statements always remain distinct; no
semantic-overlap detection is claimed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ProposedRequirement:
    """One item from the worker's raw, not-yet-canonicalized proposal."""

    component: str
    classification: str
    text: str
    source_locator: dict


@dataclass(frozen=True)
class CanonicalRequirement:
    """One merged, source-ordered requirement ready for `JR-###` assignment."""

    component: str
    classification: str
    canonical_text: str
    source_locators: list[dict]
    first_source_order: int


class RequirementConflictError(ValueError):
    """A canonical duplicate has conflicting classifications within one component."""

    def __init__(self, canonical_text: str, component: str):
        self.canonical_text = canonical_text
        self.component = component
        super().__init__(
            f"conflicting classification for canonical requirement "
            f"{canonical_text!r} in component {component!r}"
        )


def canonicalize(text: str) -> str:
    """NFKC normalize, casefold, collapse internal whitespace, strip
    surrounding Unicode punctuation."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    collapsed = " ".join(normalized.split())

    start = 0
    end = len(collapsed)
    while start < end and unicodedata.category(collapsed[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(collapsed[end - 1]).startswith("P"):
        end -= 1
    return collapsed[start:end]


def merge_duplicates(proposed: list[ProposedRequirement]) -> list[CanonicalRequirement]:
    """Group by (canonical_text, component) in first-source order; merge
    same-classification groups, retaining every locator; raise on a
    conflicting classification within a group."""
    groups: dict[tuple[str, str], list[tuple[int, ProposedRequirement]]] = {}
    order: list[tuple[str, str]] = []

    for index, item in enumerate(proposed):
        key = (canonicalize(item.text), item.component)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((index, item))

    result: list[CanonicalRequirement] = []
    for key in order:
        canonical_text, component = key
        members = groups[key]
        classifications = {item.classification for _, item in members}
        if len(classifications) > 1:
            raise RequirementConflictError(canonical_text, component)
        classification = members[0][1].classification
        first_index = min(index for index, _ in members)
        locators = [item.source_locator for _, item in members]
        result.append(
            CanonicalRequirement(
                component=component,
                classification=classification,
                canonical_text=canonical_text,
                source_locators=locators,
                first_source_order=first_index,
            )
        )

    result.sort(key=lambda r: r.first_source_order)
    return result


def assign_display_ids(canonical: list[CanonicalRequirement]) -> list[tuple[str, CanonicalRequirement]]:
    """Stable source-order `JR-001`, `JR-002`, ... zero-padded to 3 digits."""
    return [(f"JR-{i + 1:03d}", requirement) for i, requirement in enumerate(canonical)]


# The seven frozen rubric components (must string-match
# apps/gateway/src/domain/scoring_configuration.py's Component enum values,
# and apps/worker/src/domain/requirement_derivation.py's ALLOWED_COMPONENTS —
# both independently agree on the same frozen vocabulary, no shared import).
ALLOWED_COMPONENTS = frozenset(
    {
        "mandatory_skills",
        "relevant_experience",
        "responsibility_alignment",
        "preferred_skills_tools",
        "education_certifications",
        "domain_fit",
        "achievement_evidence_quality",
    }
)
ALLOWED_CLASSIFICATIONS = frozenset({"mandatory", "preferred"})

# assign_display_ids formats JR-### zero-padded to 3 digits into a
# String(8) column ("JR-" + 3 digits = 6 chars, headroom for up to 5
# digits) — 999 is already far beyond any realistic Job-Description-derived
# requirement count (AF-4's 20-Document corpus scale) and keeps the format
# guaranteed to fit without widening the column.
MAX_PROPOSED_REQUIREMENTS = 999


def parse_and_validate_proposal(proposal_json: object, job_description_text: str) -> list[ProposedRequirement]:
    """Defense-in-depth schema validation of the worker's staged proposal
    (AD-4: "the gateway deterministically and fail-closed validates
    structure") — the gateway does not trust the worker's own validation as
    sufficient. Raises ValueError on any structural violation."""
    if not isinstance(proposal_json, dict) or "items" not in proposal_json:
        raise ValueError("proposal_json must be an object with an 'items' array")
    items = proposal_json["items"]
    if not isinstance(items, list):
        raise ValueError("proposal_json['items'] must be an array")

    if len(items) > MAX_PROPOSED_REQUIREMENTS:
        # `assign_display_ids`'s JR-### numbering must fit `String(8)`
        # (review finding) — reject oversized proposals cleanly here rather
        # than let a later insert fail on data-length.
        raise ValueError(f"proposal has more than {MAX_PROPOSED_REQUIREMENTS} items")

    text_length = len(job_description_text)
    result: list[ProposedRequirement] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("every proposal item must be an object")
        component = item.get("component")
        classification = item.get("classification")
        text = item.get("text")
        source_start = item.get("source_start")
        source_end = item.get("source_end")
        if component not in ALLOWED_COMPONENTS:
            raise ValueError(f"unsupported component: {component!r}")
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise ValueError(f"unsupported classification: {classification!r}")
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")
        if not canonicalize(text):
            # A statement that canonicalizes to "" (e.g. pure punctuation
            # like "..." or "???") would silently merge with every other
            # empty-canonicalizing item under merge_duplicates' grouping key
            # (review finding) — reject it as malformed instead.
            raise ValueError(f"text canonicalizes to empty: {text!r}")
        if (
            not isinstance(source_start, int)
            or not isinstance(source_end, int)
            or isinstance(source_start, bool)
            or isinstance(source_end, bool)
            or not (0 <= source_start < source_end <= text_length)
        ):
            raise ValueError(
                f"locator out of bounds: [{source_start}, {source_end}) for text of length {text_length}"
            )
        result.append(
            ProposedRequirement(
                component=component,
                classification=classification,
                text=text,
                source_locator={"start": source_start, "end": source_end},
            )
        )
    return result
