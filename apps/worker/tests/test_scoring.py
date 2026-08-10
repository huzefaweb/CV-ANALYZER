"""Golden and boundary fixtures for the deterministic scoring calculator
(AF-6, AD-10, AD-11, AR-26 through AR-30). Pure in-memory arithmetic — no
database, network, or fixture files; Story 1.5b's Implementation Contract
scopes this to "pure calculation fixtures only"."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from src.domain.scoring import (
    BASE_WEIGHT_BPS,
    SCORING_RATIO,
    CandidateScore,
    Component,
    CriterionState,
    component_contribution_display,
    component_score,
    coverage_weight_bps,
    effective_weights_bps,
    headline_whole_percent,
    overall_score_bps,
    precise_score_percent,
    rank_order,
    total_coverage_bps,
)

# --- AF-6 Golden Candidate G-1: all seven components applicable ------------

GOLDEN_RATINGS: dict[Component, list[CriterionState]] = {
    Component.MANDATORY_SKILLS: [
        CriterionState.MATCHED,
        CriterionState.PARTIAL,
        CriterionState.NOT_FOUND,
    ],
    Component.RELEVANT_EXPERIENCE: [CriterionState.MATCHED, CriterionState.PARTIAL],
    Component.RESPONSIBILITY_ALIGNMENT: [CriterionState.MATCHED, CriterionState.MATCHED],
    Component.PREFERRED_SKILLS_TOOLS: [CriterionState.PARTIAL, CriterionState.NOT_FOUND],
    Component.EDUCATION_CERTIFICATIONS: [CriterionState.MATCHED],
    Component.DOMAIN_FIT: [CriterionState.NEEDS_VALIDATION],
    Component.ACHIEVEMENT_EVIDENCE_QUALITY: [CriterionState.MATCHED, CriterionState.PARTIAL],
}

GOLDEN_EXPECTED_COMPONENT_SCORES: dict[Component, Fraction] = {
    Component.MANDATORY_SKILLS: Fraction(1, 2),
    Component.RELEVANT_EXPERIENCE: Fraction(3, 4),
    Component.RESPONSIBILITY_ALIGNMENT: Fraction(1, 1),
    Component.PREFERRED_SKILLS_TOOLS: Fraction(1, 4),
    Component.EDUCATION_CERTIFICATIONS: Fraction(1, 1),
    Component.DOMAIN_FIT: Fraction(0, 1),
    Component.ACHIEVEMENT_EVIDENCE_QUALITY: Fraction(3, 4),
}


def _golden_component_scores() -> dict[Component, Fraction | None]:
    return {
        component: component_score([SCORING_RATIO[state] for state in ratings])
        for component, ratings in GOLDEN_RATINGS.items()
    }


def test_golden_component_scores_match_af6_table():
    scores = _golden_component_scores()
    assert scores == GOLDEN_EXPECTED_COMPONENT_SCORES


def test_golden_effective_weights_are_unmodified_base_weights():
    applicable = {c: True for c in Component}
    weights = effective_weights_bps(applicable)
    assert weights == BASE_WEIGHT_BPS
    assert sum(weights.values()) == 10_000


def test_golden_overall_score_is_exactly_6500_bps():
    scores = _golden_component_scores()
    weights = effective_weights_bps({c: True for c in Component})
    total = overall_score_bps(scores, weights)
    assert total == Fraction(6500, 1)


def test_golden_precise_and_headline_display():
    scores = _golden_component_scores()
    weights = effective_weights_bps({c: True for c in Component})
    total = overall_score_bps(scores, weights)
    assert precise_score_percent(total) == Decimal("65.00")
    assert headline_whole_percent(total) == 65


def test_golden_component_contribution_display_sums_to_precise_total():
    scores = _golden_component_scores()
    weights = effective_weights_bps({c: True for c in Component})
    total = overall_score_bps(scores, weights)
    contributions = component_contribution_display(weights, scores)
    assert sum(contributions.values()) == precise_score_percent(total)
    # Domain fit is N/A-scored-zero but still *applicable* (has a rating),
    # so it is NOT omitted — only components with score=None are omitted.
    assert set(contributions) == set(Component)


# --- N/A redistribution: Education/certifications has no applicable req ---


def test_na_redistribution_totals_exactly_10000_bps():
    applicable = {c: True for c in Component}
    applicable[Component.EDUCATION_CERTIFICATIONS] = False
    weights = effective_weights_bps(applicable)
    assert Component.EDUCATION_CERTIFICATIONS not in weights
    assert sum(weights.values()) == 10_000


def test_na_redistribution_matches_hand_computed_largest_remainder():
    # Hand-computed (see Dev Notes derivation): applicable base total = 9000,
    # N/A pool = 1000. Each applicable share = base * 10/9. Fractional
    # remainders (as base*10 mod 9 / 9): Mandatory=1/3, Relevant=7/9,
    # Responsibility=2/3, Preferred=1/9, Domain fit=5/9, Achievement=5/9.
    # Top 3 by remainder (ties by frozen order): Relevant, Responsibility,
    # Domain fit (Domain fit precedes Achievement in frozen order) each +1.
    applicable = {c: True for c in Component}
    applicable[Component.EDUCATION_CERTIFICATIONS] = False
    weights = effective_weights_bps(applicable)
    assert weights == {
        Component.MANDATORY_SKILLS: 3333,
        Component.RELEVANT_EXPERIENCE: 2778,
        Component.RESPONSIBILITY_ALIGNMENT: 1667,
        Component.PREFERRED_SKILLS_TOOLS: 1111,
        Component.DOMAIN_FIT: 556,
        Component.ACHIEVEMENT_EVIDENCE_QUALITY: 555,
    }
    assert sum(weights.values()) == 10_000


def test_na_redistribution_overall_score_reconciles_to_hand_computed_value():
    # Same golden ratings, minus Education/certifications (N/A). Component
    # scores are unaffected by weight redistribution — only the weights
    # change. Hand-computed exact contribution using the redistributed
    # weights from test_na_redistribution_matches_hand_computed_largest_remainder:
    #   Mandatory 3333 * 1/2   = 1666.5
    #   Relevant  2778 * 3/4   = 2083.5
    #   Responsibility 1667*1  = 1667
    #   Preferred 1111 * 1/4   = 277.75
    #   Domain fit 556 * 0     = 0
    #   Achievement 555 * 3/4  = 416.25
    #   sum = 1666.5+2083.5+1667+277.75+0+416.25 = 6111
    applicable = {c: True for c in Component}
    applicable[Component.EDUCATION_CERTIFICATIONS] = False
    weights = effective_weights_bps(applicable)

    scores = _golden_component_scores()
    del scores[Component.EDUCATION_CERTIFICATIONS]

    total = overall_score_bps(scores, weights)
    assert total == Fraction(6111, 1)


def test_na_redistribution_equal_remainder_tie_breaks_by_frozen_order():
    # Domain fit and Achievement/Evidence quality both land on exactly 5/9 —
    # Domain fit (earlier in frozen Component order) wins the residual bp.
    applicable = {c: True for c in Component}
    applicable[Component.EDUCATION_CERTIFICATIONS] = False
    weights = effective_weights_bps(applicable)
    assert weights[Component.DOMAIN_FIT] == 556
    assert weights[Component.ACHIEVEMENT_EVIDENCE_QUALITY] == 555


# --- Coverage weight (AD-11/AR-26) -----------------------------------------


def test_coverage_boundary_at_exactly_7000_bps_meets_threshold():
    # No gate-decision function exists in this pure-calculator module (that
    # wiring is Epic 4 Story 4.5's job); this asserts the exact-rational
    # value the eventual gate will compare, at the inclusive `>=` boundary.
    weight = coverage_weight_bps(21_000, 3)  # exactly 7000/1
    total = total_coverage_bps([(weight, CriterionState.MATCHED)])
    assert total == Fraction(7000, 1)
    assert total >= Fraction(7000, 1)


def test_coverage_boundary_just_below_7000_bps_fails_without_float_drift():
    weight = coverage_weight_bps(20_999, 3)  # 6999.666...7, exact
    total = total_coverage_bps([(weight, CriterionState.MATCHED)])
    assert total == Fraction(20_999, 3)
    assert total < Fraction(7000, 1)


def test_coverage_excludes_needs_validation():
    weight = coverage_weight_bps(7000, 1)
    total = total_coverage_bps([(weight, CriterionState.NEEDS_VALIDATION)])
    assert total == Fraction(0, 1)


# --- Exact tie-breaking and ranking (AR-30) --------------------------------


def test_exact_tie_breaks_by_mandatory_then_experience_score():
    higher_mandatory = CandidateScore(
        overall_score_bps=Fraction(6500, 1),
        mandatory_skills_score=Fraction(3, 4),
        relevant_experience_score=Fraction(1, 2),
        candidate_key="candidate-b",
    )
    lower_mandatory = CandidateScore(
        overall_score_bps=Fraction(6500, 1),
        mandatory_skills_score=Fraction(1, 2),
        relevant_experience_score=Fraction(1, 2),
        candidate_key="candidate-a",
    )
    ranked = rank_order([lower_mandatory, higher_mandatory])
    assert ranked == [higher_mandatory, lower_mandatory]
    assert ranked[0].overall_score_bps == ranked[1].overall_score_bps


def test_close_exact_scores_order_correctly_despite_identical_display():
    # Both round to 6.66% displayed, but B's exact value is genuinely
    # higher — rank_order must use the exact Fraction, never the rounded
    # display, to order them (SOLUTION-DESIGN.md §7.3).
    lower_exact = CandidateScore(
        overall_score_bps=Fraction(66_625, 100),  # 666.25 bps = 6.6625%
        mandatory_skills_score=None,
        relevant_experience_score=None,
        candidate_key="candidate-a",
    )
    higher_exact = CandidateScore(
        overall_score_bps=Fraction(66_649, 100),  # 666.49 bps = 6.6649%
        mandatory_skills_score=None,
        relevant_experience_score=None,
        candidate_key="candidate-b",
    )
    assert precise_score_percent(lower_exact.overall_score_bps) == precise_score_percent(
        higher_exact.overall_score_bps
    )
    ranked = rank_order([lower_exact, higher_exact])
    assert ranked == [higher_exact, lower_exact]


# --- Round-half-up display boundaries (AR-29) ------------------------------


def test_precise_score_percent_rounds_half_up_at_exact_boundary():
    # 62.505% is exactly halfway between 62.50 and 62.51; ROUND_HALF_UP
    # rounds away from zero, never Decimal's default ROUND_HALF_EVEN.
    total = Fraction(12_501, 2)  # 6250.5 bps = 62.505%
    assert precise_score_percent(total) == Decimal("62.51")


def test_headline_whole_percent_rounds_half_up_at_exact_boundary():
    total = Fraction(6250, 1)  # 62.50% exactly
    assert headline_whole_percent(total) == 63


# --- Guards -----------------------------------------------------------------


def test_component_score_of_empty_ratings_is_na_not_a_crash():
    assert component_score([]) is None


def test_effective_weights_requires_at_least_one_applicable_component():
    with pytest.raises(ValueError):
        effective_weights_bps({c: False for c in Component})


def test_effective_weights_rejects_missing_component_key():
    incomplete = {c: True for c in Component if c is not Component.DOMAIN_FIT}
    with pytest.raises(ValueError):
        effective_weights_bps(incomplete)


def test_coverage_weight_rejects_zero_requirement_count():
    with pytest.raises(ValueError):
        coverage_weight_bps(1000, 0)


def test_component_score_rejects_non_fraction_rating():
    with pytest.raises(TypeError):
        component_score([0.5])  # type: ignore[list-item]


def test_overall_score_rejects_mismatched_scores_and_weights():
    # A component present (non-None) in component_scores but absent from
    # effective_weights means the two maps were computed from different
    # `applicable` inputs — must fail loudly, not raise a bare KeyError or
    # silently contribute zero.
    scores = {Component.MANDATORY_SKILLS: Fraction(1, 2)}
    weights: dict[Component, int] = {}
    with pytest.raises(ValueError):
        overall_score_bps(scores, weights)


def test_component_score_supports_genuinely_repeating_rational_means():
    # SOLUTION-DESIGN.md §7.3 requires golden fixtures to cover "repeating
    # rational means" — a mean whose reduced denominator is not a power of
    # 2 or 5, so it has no terminating decimal representation.
    score = component_score(
        [SCORING_RATIO[CriterionState.MATCHED], SCORING_RATIO[CriterionState.NOT_FOUND], SCORING_RATIO[CriterionState.NOT_FOUND]]
    )
    assert score == Fraction(1, 3)
