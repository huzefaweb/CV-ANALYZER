"""Scoring Configuration effective-weight computation (AF-6, AD-4) — pure,
no DB/web/SDK imports.

Ported (not imported) from `apps/worker/src/domain/scoring.py`'s
`Component`/`BASE_WEIGHT_BPS`/`effective_weights_bps` — `apps/gateway` and
`apps/worker` are separate deployable Python packages with no shared
package in this repo, and AD-4 explicitly assigns basis-point weight
computation to the gateway, not the worker. See 3-5's Dev Notes for the
full reasoning.
"""

from __future__ import annotations

from enum import Enum
from fractions import Fraction
from typing import Mapping


class Component(str, Enum):
    """The seven rubric components in frozen order — declaration order IS
    the frozen order for largest-remainder tie-breaking (AF-6 step 6)."""

    MANDATORY_SKILLS = "mandatory_skills"
    RELEVANT_EXPERIENCE = "relevant_experience"
    RESPONSIBILITY_ALIGNMENT = "responsibility_alignment"
    PREFERRED_SKILLS_TOOLS = "preferred_skills_tools"
    EDUCATION_CERTIFICATIONS = "education_certifications"
    DOMAIN_FIT = "domain_fit"
    ACHIEVEMENT_EVIDENCE_QUALITY = "achievement_evidence_quality"


BASE_WEIGHT_BPS: dict[Component, int] = {
    Component.MANDATORY_SKILLS: 3000,
    Component.RELEVANT_EXPERIENCE: 2500,
    Component.RESPONSIBILITY_ALIGNMENT: 1500,
    Component.PREFERRED_SKILLS_TOOLS: 1000,
    Component.EDUCATION_CERTIFICATIONS: 1000,
    Component.DOMAIN_FIT: 500,
    Component.ACHIEVEMENT_EVIDENCE_QUALITY: 500,
}
TOTAL_BASE_WEIGHT_BPS = sum(BASE_WEIGHT_BPS.values())
assert TOTAL_BASE_WEIGHT_BPS == 10_000


def _largest_remainder_allocate(
    exact_shares: Mapping[Component, Fraction], total_units: int
) -> dict[Component, int]:
    floors: dict[Component, int] = {}
    remainders: dict[Component, Fraction] = {}
    for component in Component:
        if component not in exact_shares:
            continue
        share = exact_shares[component]
        floor_value = share.numerator // share.denominator
        floors[component] = floor_value
        remainders[component] = share - floor_value

    residual = total_units - sum(floors.values())
    ordered = [c for c in Component if c in remainders]
    ordered.sort(key=lambda c: (-remainders[c], list(Component).index(c)))
    for component in ordered[:residual]:
        floors[component] += 1
    return floors


def effective_weights_bps(applicable: Mapping[Component, bool]) -> dict[Component, int]:
    """N/A base-weight redistribution by integer largest remainder, frozen
    component order (AF-6 step 6). Result sums to exactly 10,000 whenever at
    least one component is applicable; raises `ValueError` when none are
    (AF-6 step 7: a Job Description with zero applicable requirements is
    rejected before analysis)."""
    missing = set(Component) - set(applicable)
    if missing:
        raise ValueError(f"applicable is missing explicit entries for: {sorted(missing)}")

    applicable_components = [c for c in Component if applicable[c]]
    if not applicable_components:
        raise ValueError("at least one component must be applicable")

    if len(applicable_components) == len(Component):
        return dict(BASE_WEIGHT_BPS)

    applicable_base_total = sum(BASE_WEIGHT_BPS[c] for c in applicable_components)
    na_pool = TOTAL_BASE_WEIGHT_BPS - applicable_base_total

    exact_shares = {
        c: Fraction(BASE_WEIGHT_BPS[c], 1)
        + Fraction(BASE_WEIGHT_BPS[c] * na_pool, applicable_base_total)
        for c in applicable_components
    }
    return _largest_remainder_allocate(exact_shares, TOTAL_BASE_WEIGHT_BPS)


def applicable_components(components_with_requirements: set[str]) -> dict[Component, bool]:
    """`True` for every component with at least one accepted Job
    Requirement (by string value), `False` otherwise."""
    return {component: component.value in components_with_requirements for component in Component}
