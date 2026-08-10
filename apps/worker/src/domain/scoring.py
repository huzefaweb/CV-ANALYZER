"""Deterministic scoring calculator (AF-6, AD-10, AD-11) — pure functions,
exact rational arithmetic only, no DB/network/framework imports. Enforced by
tests/test_domain_boundary.py.

Every intermediate value (criterion ratio, component score, effective
weight's rational share, overall score, coverage weight) is a
`fractions.Fraction` — never `float` — until the two dedicated display
functions (`precise_score_percent`, `headline_whole_percent`) convert to
`Decimal` for presentation only (AR-27, AR-29). Display values never feed
back into scoring or ranking.

`coverage_weight_bps`/`total_coverage_bps` provide the exact-rational
scoreability formula from AD-11/AR-26; wiring them into an actual
`COVERAGE_BELOW_7000_BPS` gate decision remains out of scope (see
parse_gates.py's own docstring — that gate is Epic 4 Story 4.5's job).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import Enum
from fractions import Fraction
from typing import Mapping, Sequence


class Component(str, Enum):
    """The seven rubric components in frozen order (AD-10/SOLUTION-DESIGN
    §7.1). Enum member declaration order IS the frozen order — every
    largest-remainder tie-break in this module iterates `Component` members
    in this order, never alphabetically or by weight."""

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


class CriterionState(Enum):
    """The four Analysis states (AF-6 formula step 4). NOT_FOUND and
    NEEDS_VALIDATION score identically (see SCORING_RATIO) but must stay
    distinct enum members — collapsing them via equal-valued members would
    make Python's Enum alias one to the other, silently losing the
    distinction Evidence/coverage/display all depend on. Member values here
    are therefore arbitrary sentinels, not the scoring ratio; use
    SCORING_RATIO[state] for the ratio."""

    MATCHED = "matched"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    NEEDS_VALIDATION = "needs_validation"


SCORING_RATIO: dict[CriterionState, Fraction] = {
    CriterionState.MATCHED: Fraction(1, 1),
    CriterionState.PARTIAL: Fraction(1, 2),
    CriterionState.NOT_FOUND: Fraction(0, 1),
    CriterionState.NEEDS_VALIDATION: Fraction(0, 1),
}


def component_score(ratings: Sequence[Fraction]) -> Fraction | None:
    """Arithmetic mean of criterion ratios in one component (AF-6 step 5).
    `None` means N/A — no applicable Job Requirement in this component
    (AF-6 step 6). Rejects any non-`Fraction` rating (e.g. `float`) — this
    is the ingestion boundary for the exact-rational pipeline; a stray
    `float` here would silently coerce every downstream sum/product to
    `float` (AR-27's "no binary floating point... anywhere in the scoring
    path" would otherwise be violated without any exception)."""
    for rating in ratings:
        if not isinstance(rating, Fraction):
            raise TypeError(f"rating must be a Fraction, got {type(rating).__name__}")
    if not ratings:
        return None
    return sum(ratings, Fraction(0, 1)) / len(ratings)


def _largest_remainder_allocate(
    exact_shares: Mapping[Component, Fraction], total_units: int
) -> dict[Component, int]:
    """Shared largest-remainder mechanic (AR-28/AR-29): floor every exact
    share, then distribute the residual whole units one at a time to the
    components with the largest discarded fractional remainder, frozen
    Component order breaking ties. Callers pass different quantities
    (weight redistribution vs. display-rounding residuals) through the same
    rule."""
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
    component order (AF-6 step 6, AD-10, AR-28). Result sums to exactly
    10,000 whenever at least one component is applicable.

    `applicable` must have an explicit entry for every `Component` — a
    missing key is a caller bug (an omitted component, not a deliberate
    N/A) and must not be silently treated as `False`."""
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


def _require_scored_components_have_weights(
    component_scores: Mapping[Component, Fraction | None],
    effective_weights: Mapping[Component, int],
) -> None:
    """`component_scores` and `effective_weights` must describe the same
    applicable set — a component with a non-`None` score but no entry in
    `effective_weights` (or vice versa) means the two maps were computed
    from different `applicable` inputs, a caller bug that must fail loudly
    rather than surface as a bare `KeyError` or silently contribute zero."""
    scored = {c for c, score in component_scores.items() if score is not None}
    weighted = set(effective_weights)
    if scored != weighted:
        raise ValueError(
            "component_scores and effective_weights disagree on the applicable "
            f"set: scored-only={sorted(scored - weighted)}, "
            f"weighted-only={sorted(weighted - scored)}"
        )


def overall_score_bps(
    component_scores: Mapping[Component, Fraction | None],
    effective_weights: Mapping[Component, int],
) -> Fraction:
    """Exact overall score in basis points (AF-6 step 8, SOLUTION-DESIGN
    §7.2): sum of each applicable component's weight times its score. N/A
    components (score `None`) contribute nothing."""
    _require_scored_components_have_weights(component_scores, effective_weights)
    total = Fraction(0, 1)
    for component, score in component_scores.items():
        if score is None:
            continue
        total += effective_weights[component] * score
    return total


def coverage_weight_bps(effective_component_bps: int, applicable_requirement_count: int) -> Fraction:
    """Exact per-requirement coverage weight (AD-11/AR-26)."""
    if applicable_requirement_count <= 0:
        raise ValueError("applicable_requirement_count must be positive")
    return Fraction(effective_component_bps, applicable_requirement_count)


def total_coverage_bps(per_requirement: Sequence[tuple[Fraction, CriterionState]]) -> Fraction:
    """Sum of coverage weight for every requirement whose state is not
    NEEDS_VALIDATION (Matched, Partial, and Not Found all count; Needs
    Validation contributes zero coverage) — AD-11."""
    total = Fraction(0, 1)
    for weight, state in per_requirement:
        if state is CriterionState.NEEDS_VALIDATION:
            continue
        total += weight
    return total


@dataclass(frozen=True)
class CandidateScore:
    """One Candidate's exact scoring result plus the immutable, non-sensitive
    key used only to stabilize tied presentation order (AR-30) — this key
    carries no hiring meaning."""

    overall_score_bps: Fraction
    mandatory_skills_score: Fraction | None
    relevant_experience_score: Fraction | None
    candidate_key: str


def rank_order(scores: Sequence[CandidateScore]) -> list[CandidateScore]:
    """Descending by exact overall_score_bps (a precise tie exists only at
    exact equality, never a display-rounded tolerance); ties broken by exact
    mandatory-skills score, then exact relevant-experience score (both
    descending, `None` sorts lowest), then ascending candidate_key for final
    stability (AR-30). This ordering carries no hiring meaning."""

    def key(candidate: CandidateScore) -> tuple[Fraction, Fraction, Fraction, str]:
        mandatory = candidate.mandatory_skills_score
        experience = candidate.relevant_experience_score
        return (
            -candidate.overall_score_bps,
            -(mandatory if mandatory is not None else Fraction(-1, 1)),
            -(experience if experience is not None else Fraction(-1, 1)),
            candidate.candidate_key,
        )

    return sorted(scores, key=key)


def _to_decimal(value: Fraction, precision: int = 40) -> Decimal:
    """Exact Fraction -> Decimal conversion (never via float)."""
    with localcontext() as ctx:
        ctx.prec = precision
        return Decimal(value.numerator) / Decimal(value.denominator)


def precise_score_percent(overall_score_bps_value: Fraction) -> Decimal:
    """Percentage to two decimal places, decimal ROUND_HALF_UP applied only
    after the exact total is known (AR-29)."""
    percent = _to_decimal(overall_score_bps_value) / Decimal(100)
    return percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def headline_whole_percent(overall_score_bps_value: Fraction) -> int:
    """Ranked Results headline: whole percent, decimal ROUND_HALF_UP (AR-29)."""
    percent = _to_decimal(overall_score_bps_value) / Decimal(100)
    return int(percent.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def component_contribution_display(
    effective_weights: Mapping[Component, int],
    component_scores: Mapping[Component, Fraction | None],
) -> dict[Component, Decimal]:
    """Two-decimal-percentage contribution per component, floored to one
    basis point then reconciled by largest remainder so the displayed
    contributions sum exactly to `precise_score_percent`'s total (AR-29,
    SOLUTION-DESIGN §7.3). N/A components are omitted."""
    _require_scored_components_have_weights(component_scores, effective_weights)
    exact_contribution_bps: dict[Component, Fraction] = {
        component: effective_weights[component] * score
        for component, score in component_scores.items()
        if score is not None
    }
    total_bps_value = overall_score_bps(component_scores, effective_weights)
    displayed_total_bps = precise_score_percent(total_bps_value) * 100

    allocated = _largest_remainder_allocate(
        exact_contribution_bps, int(displayed_total_bps)
    )
    return {component: Decimal(bps) / Decimal(100) for component, bps in allocated.items()}
