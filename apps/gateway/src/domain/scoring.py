"""Deterministic scoring calculator (AF-5, AF-6, AD-10, AD-11) — pure
functions, exact rational arithmetic only, no DB/network/framework imports.
Enforced by tests/test_domain_boundary.py.

Ported (not imported) from `apps/worker/src/domain/scoring.py` (Story
1.5b) — `apps/gateway` and `apps/worker` are separate deployable Python
packages with no shared package in this repo, and AR-17 explicitly assigns
scoring to the gateway ("The worker ... cannot ... score, decide
scoreability"). `Component`/`BASE_WEIGHT_BPS`/`_largest_remainder_allocate`
are imported from the sibling `scoring_configuration` module (Story 3.5) —
that is a same-package import, not a cross-boundary one.

Every intermediate value (criterion ratio, component score, overall score,
coverage weight) is a `fractions.Fraction` — never `float` — until the two
dedicated display functions (`precise_score_percent`,
`headline_whole_percent`) convert to `Decimal` for presentation only
(AR-27, AR-29). Display values never feed back into scoring or ranking.

`evaluate_candidate_scoreability` is this story's new orchestration
function: it combines the readable-content gate (Story 4.2's
`parse_artifacts.gate_codes`), the budget-overflow gate (Story 4.3/4.4's
`candidate_proposals.gate_codes`), and the criterion-coverage gate computed
here into one Needs Review/scoreable decision (AF-5). It is a pure
calculation over given inputs — no DB read, no provider call, no mutation.
Wiring real `apps/gateway/src/adapters/models.py` rows into this function's
plain-dict parameters is Story 4.6's job, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from enum import Enum
from fractions import Fraction
from typing import Mapping, Sequence

from .scoring_configuration import Component, _largest_remainder_allocate

COVERAGE_BELOW_7000_BPS = "COVERAGE_BELOW_7000_BPS"
"""Frozen public gate-reason code (AF-5), matching
`apps/worker/src/domain/parse_gates.GateCode.COVERAGE_BELOW_7000_BPS`'s
`.value` exactly — a literal-string port of one constant, not an enum
import."""

CRITERION_COVERAGE_THRESHOLD_BPS = Fraction(7000, 1)
"""AF-5/AD-11's frozen criterion-coverage gate threshold — named so the
value backing `COVERAGE_BELOW_7000_BPS`'s name and the comparison it gates
can't silently drift apart."""


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

_LABEL_TO_STATE: dict[str, CriterionState] = {
    "Matched": CriterionState.MATCHED,
    "Partial": CriterionState.PARTIAL,
    "Not Found": CriterionState.NOT_FOUND,
    "Needs Validation": CriterionState.NEEDS_VALIDATION,
}


def criterion_state_from_label(label: str) -> CriterionState:
    """Maps the four frozen AF-6 state labels as they appear on the wire in
    `candidate_proposals.items_json` (`apps/worker/src/domain/
    analysis_provider.AnalysisState`'s exact `.value` strings, ported here
    as literal strings). Raises `ValueError` on any other label — an
    unrecognized label reaching this layer is a caller-contract violation,
    not a silently-ignored item."""
    try:
        return _LABEL_TO_STATE[label]
    except KeyError:
        raise ValueError(f"unrecognized criterion state label: {label!r}") from None


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


def _require_scored_components_have_weights(
    component_scores: Mapping[Component, Fraction | None],
    effective_weights: Mapping[Component, int],
) -> None:
    """`component_scores` and `effective_weights` must describe the same
    applicable set — a component with a non-`None` score but no entry in
    `effective_weights` (or vice versa) means the two maps were computed
    from different inputs, a caller bug that must fail loudly rather than
    surface as a bare `KeyError` or silently contribute zero."""
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
    carries no hiring meaning. `candidate_key` must be an opaque identifier
    (a Candidate id/UUID) — never identity/contact (CLAUDE.md's
    non-negotiable: candidate identity/contact is never used in scoring)."""

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

    allocated = _largest_remainder_allocate(exact_contribution_bps, int(displayed_total_bps))
    return {component: Decimal(bps) / Decimal(100) for component, bps in allocated.items()}


@dataclass(frozen=True)
class CandidateScoreability:
    """Outcome of `evaluate_candidate_scoreability` — either a suppressed
    Needs Review (score/rank fields all `None`) or a scoreable Candidate
    carrying the full exact result."""

    needs_review: bool
    gate_codes: tuple[str, ...]
    score: CandidateScore | None
    precise_score_percent: Decimal | None
    headline_whole_percent: int | None
    component_contribution_display: dict[Component, Decimal] | None
    coverage_bps: Fraction | None


def evaluate_candidate_scoreability(
    *,
    parse_gate_codes: Sequence[str],
    proposal_gate_codes: Sequence[str],
    proposal_items: Sequence[Mapping[str, str]],
    requirement_components: Mapping[str, Component],
    effective_weights: Mapping[Component, int],
    candidate_key: str,
) -> CandidateScoreability:
    """AF-5's two-gate scoreability decision plus AF-6's score calculation,
    applied to one Candidate's already-validated, already-frozen inputs.

    `proposal_items` entries are `{"job_requirement_id": str, "state": str}`
    — the shape `candidate_proposals.items_json` already persists (Story
    4.4); locator/excerpt fields are irrelevant to scoring.
    `requirement_components` maps every frozen job_requirement_id to its
    Component (`job_requirements.component`). `effective_weights` is the
    already-frozen `scoring_configurations` output (Story 3.5) filtered to
    `applicable = True` rows — never recomputed here.
    """
    if parse_gate_codes or proposal_gate_codes:
        # Readable-content and/or budget-overflow gate already fired.
        # Never compute coverage/score on this path (AC#3: no coercion to
        # a low score) — the readable-content gate can fire even when
        # proposal_items is non-empty (Story 4.2's gate is independent of
        # the provider call), and budget overflow always carries empty
        # proposal_items with this gate code already set.
        return CandidateScoreability(
            needs_review=True,
            gate_codes=tuple(parse_gate_codes) + tuple(proposal_gate_codes),
            score=None,
            precise_score_percent=None,
            headline_whole_percent=None,
            component_contribution_display=None,
            coverage_bps=None,
        )

    if not proposal_items:
        raise ValueError(
            "proposal_items is empty with no gate codes present — a validated "
            "Story 4.4 proposal always covers every requested requirement "
            "unless a gate (e.g. budget overflow) already fired"
        )

    proposal_ids = [item["job_requirement_id"] for item in proposal_items]
    if len(proposal_ids) != len(requirement_components) or set(proposal_ids) != set(requirement_components):
        # Catches all three shapes of a mismatched proposal/requirement pair
        # in one check: an id in proposal_items absent from
        # requirement_components, an id in requirement_components missing
        # from proposal_items (partial coverage — would otherwise silently
        # under-count that component's score/coverage instead of raising),
        # and a duplicate job_requirement_id (would otherwise silently
        # double-count). A validated Story 4.4 proposal never produces any
        # of these against its own frozen requirements, but this function's
        # own inputs are caller-assembled (Story 4.6's future wiring), so
        # this boundary fails loudly rather than trusting silently.
        raise ValueError(
            "proposal_items does not exactly match requirement_components: "
            f"missing={sorted(set(requirement_components) - set(proposal_ids))}, "
            f"unexpected_or_duplicate={sorted(set(proposal_ids) - set(requirement_components))}"
        )

    components_with_requirements = set(requirement_components.values())
    missing_weights = components_with_requirements - set(effective_weights)
    if missing_weights:
        raise ValueError(
            f"effective_weights is missing entries for components with requirements: {sorted(missing_weights)}"
        )

    requirement_counts: dict[Component, int] = {}
    for component in requirement_components.values():
        requirement_counts[component] = requirement_counts.get(component, 0) + 1

    ratings_by_component: dict[Component, list[Fraction]] = {}
    coverage_pairs: list[tuple[Fraction, CriterionState]] = []
    for item in proposal_items:
        state = criterion_state_from_label(item["state"])
        component = requirement_components[item["job_requirement_id"]]
        ratings_by_component.setdefault(component, []).append(SCORING_RATIO[state])
        weight = coverage_weight_bps(effective_weights[component], requirement_counts[component])
        coverage_pairs.append((weight, state))

    coverage = total_coverage_bps(coverage_pairs)
    if coverage < CRITERION_COVERAGE_THRESHOLD_BPS:
        return CandidateScoreability(
            needs_review=True,
            gate_codes=(COVERAGE_BELOW_7000_BPS,),
            score=None,
            precise_score_percent=None,
            headline_whole_percent=None,
            component_contribution_display=None,
            coverage_bps=coverage,
        )

    component_scores: dict[Component, Fraction | None] = {
        component: component_score(ratings) for component, ratings in ratings_by_component.items()
    }
    overall = overall_score_bps(component_scores, effective_weights)
    score = CandidateScore(
        overall_score_bps=overall,
        mandatory_skills_score=component_scores.get(Component.MANDATORY_SKILLS),
        relevant_experience_score=component_scores.get(Component.RELEVANT_EXPERIENCE),
        candidate_key=candidate_key,
    )
    return CandidateScoreability(
        needs_review=False,
        gate_codes=(),
        score=score,
        precise_score_percent=precise_score_percent(overall),
        headline_whole_percent=headline_whole_percent(overall),
        component_contribution_display=component_contribution_display(effective_weights, component_scores),
        coverage_bps=coverage,
    )
