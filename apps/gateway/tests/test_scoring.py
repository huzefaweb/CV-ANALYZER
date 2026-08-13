"""Golden and boundary fixtures for the gateway-side deterministic scoring
calculator (AF-5, AF-6, AD-10, AD-11, AR-26 through AR-30). Pure in-memory
arithmetic — no database, network, or fixture files; this story's
Implementation Contract scopes it to a pure calculation kernel, mirroring
Story 1.5b's "pure calculation fixtures only" boundary for the worker-side
proof.

Fixtures are shaped like the real gateway tables (`job_requirements.component`,
`scoring_configurations.effective_weight_bps`, `candidate_proposals.items_json`/
`gate_codes`, `parse_artifacts.gate_codes`) `evaluate_candidate_scoreability`
will eventually be called with, per this story's own scope boundary."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from src.domain.scoring import (
    COVERAGE_BELOW_7000_BPS,
    CandidateScore,
    CriterionState,
    component_score,
    coverage_weight_bps,
    criterion_state_from_label,
    evaluate_candidate_scoreability,
    headline_whole_percent,
    precise_score_percent,
    rank_order,
    total_coverage_bps,
)
from src.domain.scoring_configuration import BASE_WEIGHT_BPS, Component

# --- AF-6 Golden Candidate G-1, expressed as gateway-shaped inputs ---------

GOLDEN_REQUIREMENT_COMPONENTS: dict[str, Component] = {
    "req-m1": Component.MANDATORY_SKILLS,
    "req-m2": Component.MANDATORY_SKILLS,
    "req-m3": Component.MANDATORY_SKILLS,
    "req-r1": Component.RELEVANT_EXPERIENCE,
    "req-r2": Component.RELEVANT_EXPERIENCE,
    "req-x1": Component.RESPONSIBILITY_ALIGNMENT,
    "req-x2": Component.RESPONSIBILITY_ALIGNMENT,
    "req-p1": Component.PREFERRED_SKILLS_TOOLS,
    "req-p2": Component.PREFERRED_SKILLS_TOOLS,
    "req-e1": Component.EDUCATION_CERTIFICATIONS,
    "req-d1": Component.DOMAIN_FIT,
    "req-a1": Component.ACHIEVEMENT_EVIDENCE_QUALITY,
    "req-a2": Component.ACHIEVEMENT_EVIDENCE_QUALITY,
}

GOLDEN_PROPOSAL_ITEMS: list[dict[str, str]] = [
    {"job_requirement_id": "req-m1", "state": "Matched"},
    {"job_requirement_id": "req-m2", "state": "Partial"},
    {"job_requirement_id": "req-m3", "state": "Not Found"},
    {"job_requirement_id": "req-r1", "state": "Matched"},
    {"job_requirement_id": "req-r2", "state": "Partial"},
    {"job_requirement_id": "req-x1", "state": "Matched"},
    {"job_requirement_id": "req-x2", "state": "Matched"},
    {"job_requirement_id": "req-p1", "state": "Partial"},
    {"job_requirement_id": "req-p2", "state": "Not Found"},
    {"job_requirement_id": "req-e1", "state": "Matched"},
    {"job_requirement_id": "req-d1", "state": "Needs Validation"},
    {"job_requirement_id": "req-a1", "state": "Matched"},
    {"job_requirement_id": "req-a2", "state": "Partial"},
]

GOLDEN_WEIGHTS: dict[Component, int] = dict(BASE_WEIGHT_BPS)


def test_golden_candidate_is_scoreable_with_exact_65_00_overall():
    assert sum(GOLDEN_WEIGHTS.values()) == 10_000
    result = evaluate_candidate_scoreability(
        parse_gate_codes=[],
        proposal_gate_codes=[],
        proposal_items=GOLDEN_PROPOSAL_ITEMS,
        requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
        effective_weights=GOLDEN_WEIGHTS,
        candidate_key="candidate-golden",
    )
    assert result.needs_review is False
    assert result.gate_codes == ()
    assert result.score is not None
    assert result.score.overall_score_bps == Fraction(6500, 1)
    assert result.precise_score_percent == Decimal("65.00")
    assert result.headline_whole_percent == 65
    assert result.component_contribution_display is not None
    assert sum(result.component_contribution_display.values()) == Decimal("65.00")
    # Domain fit contributes a Needs Validation coverage exclusion; every
    # other component's evaluated coverage is exact — coverage still passes.
    assert result.coverage_bps == Fraction(9500, 1)


# --- N/A redistribution: Education/certifications has no applicable req ---
# Weights reflect Story 3.5's already-frozen redistribution output (hand
# derived identically to Story 1.5b's own N/A fixture) — never recomputed
# by this story's kernel.

NA_REQUIREMENT_COMPONENTS: dict[str, Component] = {
    k: v for k, v in GOLDEN_REQUIREMENT_COMPONENTS.items() if v is not Component.EDUCATION_CERTIFICATIONS
}
NA_PROPOSAL_ITEMS = [item for item in GOLDEN_PROPOSAL_ITEMS if item["job_requirement_id"] != "req-e1"]
NA_WEIGHTS: dict[Component, int] = {
    Component.MANDATORY_SKILLS: 3333,
    Component.RELEVANT_EXPERIENCE: 2778,
    Component.RESPONSIBILITY_ALIGNMENT: 1667,
    Component.PREFERRED_SKILLS_TOOLS: 1111,
    Component.DOMAIN_FIT: 556,
    Component.ACHIEVEMENT_EVIDENCE_QUALITY: 555,
}


def test_na_redistribution_reconciles_to_10000_and_hand_computed_overall():
    assert sum(NA_WEIGHTS.values()) == 10_000
    result = evaluate_candidate_scoreability(
        parse_gate_codes=[],
        proposal_gate_codes=[],
        proposal_items=NA_PROPOSAL_ITEMS,
        requirement_components=NA_REQUIREMENT_COMPONENTS,
        effective_weights=NA_WEIGHTS,
        candidate_key="candidate-na",
    )
    assert result.needs_review is False
    assert result.score is not None
    # Hand-computed in Story 1.5b's own N/A fixture: 1666.5+2083.5+1667+277.75+0+416.25 = 6111.
    assert result.score.overall_score_bps == Fraction(6111, 1)


# --- Gate suppression (AF-5, AC#3) -----------------------------------------


def test_readable_content_gate_suppresses_score_even_with_full_proposal():
    # The readable-content gate (Story 4.2) can fire even when the provider
    # still ran and produced a full proposal — Step 1 must short-circuit
    # before touching coverage/score computation at all.
    result = evaluate_candidate_scoreability(
        parse_gate_codes=["TEXT_BELOW_500"],
        proposal_gate_codes=[],
        proposal_items=GOLDEN_PROPOSAL_ITEMS,
        requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
        effective_weights=GOLDEN_WEIGHTS,
        candidate_key="candidate-unreadable",
    )
    assert result.needs_review is True
    assert result.gate_codes == ("TEXT_BELOW_500",)
    assert result.score is None
    assert result.coverage_bps is None


def test_budget_overflow_gate_suppresses_score_with_empty_proposal():
    result = evaluate_candidate_scoreability(
        parse_gate_codes=[],
        proposal_gate_codes=[COVERAGE_BELOW_7000_BPS],
        proposal_items=[],
        requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
        effective_weights=GOLDEN_WEIGHTS,
        candidate_key="candidate-overflow",
    )
    assert result.needs_review is True
    assert result.gate_codes == (COVERAGE_BELOW_7000_BPS,)
    assert result.score is None


def test_criterion_coverage_gate_boundary_at_exactly_7000_bps_passes():
    requirement_components = {"req-1": Component.MANDATORY_SKILLS, "req-2": Component.MANDATORY_SKILLS, "req-3": Component.MANDATORY_SKILLS}
    weights = {Component.MANDATORY_SKILLS: 21_000}  # unrealistic total, isolates the boundary arithmetic
    items = [
        {"job_requirement_id": "req-1", "state": "Matched"},
        {"job_requirement_id": "req-2", "state": "Needs Validation"},
        {"job_requirement_id": "req-3", "state": "Needs Validation"},
    ]
    result = evaluate_candidate_scoreability(
        parse_gate_codes=[],
        proposal_gate_codes=[],
        proposal_items=items,
        requirement_components=requirement_components,
        effective_weights=weights,
        candidate_key="candidate-boundary",
    )
    assert result.coverage_bps == Fraction(7000, 1)
    assert result.needs_review is False


def test_criterion_coverage_gate_boundary_just_below_7000_bps_fails():
    requirement_components = {"req-1": Component.MANDATORY_SKILLS, "req-2": Component.MANDATORY_SKILLS, "req-3": Component.MANDATORY_SKILLS}
    weights = {Component.MANDATORY_SKILLS: 20_999}
    items = [
        {"job_requirement_id": "req-1", "state": "Matched"},
        {"job_requirement_id": "req-2", "state": "Needs Validation"},
        {"job_requirement_id": "req-3", "state": "Needs Validation"},
    ]
    result = evaluate_candidate_scoreability(
        parse_gate_codes=[],
        proposal_gate_codes=[],
        proposal_items=items,
        requirement_components=requirement_components,
        effective_weights=weights,
        candidate_key="candidate-below-boundary",
    )
    assert result.coverage_bps == Fraction(20_999, 3)
    assert result.needs_review is True
    assert result.gate_codes == (COVERAGE_BELOW_7000_BPS,)
    assert result.score is None


def test_single_needs_validation_criterion_does_not_suppress_score_when_coverage_still_passes():
    # AF-5: "A single ambiguous criterion can be Needs Validation without
    # suppressing the score when the 7,000-basis-point coverage gate still
    # passes."
    items = [dict(item) for item in GOLDEN_PROPOSAL_ITEMS]
    for item in items:
        if item["job_requirement_id"] == "req-m3":
            item["state"] = "Needs Validation"
    result = evaluate_candidate_scoreability(
        parse_gate_codes=[],
        proposal_gate_codes=[],
        proposal_items=items,
        requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
        effective_weights=GOLDEN_WEIGHTS,
        candidate_key="candidate-ambiguous",
    )
    assert result.needs_review is False
    assert result.score is not None
    assert result.coverage_bps == Fraction(8500, 1)


# --- Guards -----------------------------------------------------------------


def test_both_gate_categories_firing_simultaneously_concatenates_all_codes():
    result = evaluate_candidate_scoreability(
        parse_gate_codes=["TEXT_BELOW_500"],
        proposal_gate_codes=[COVERAGE_BELOW_7000_BPS],
        proposal_items=[],
        requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
        effective_weights=GOLDEN_WEIGHTS,
        candidate_key="candidate-both-gates",
    )
    assert result.needs_review is True
    assert result.gate_codes == ("TEXT_BELOW_500", COVERAGE_BELOW_7000_BPS)
    assert result.score is None


def test_unknown_job_requirement_id_in_proposal_items_raises_clear_value_error():
    items = [dict(item) for item in GOLDEN_PROPOSAL_ITEMS]
    items[0] = {"job_requirement_id": "req-does-not-exist", "state": "Matched"}
    with pytest.raises(ValueError):
        evaluate_candidate_scoreability(
            parse_gate_codes=[],
            proposal_gate_codes=[],
            proposal_items=items,
            requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
            effective_weights=GOLDEN_WEIGHTS,
            candidate_key="candidate-unknown-id",
        )


def test_partial_proposal_missing_one_requirement_raises_instead_of_undercounting():
    # req-m3 silently dropped from the proposal — must raise, not silently
    # compute mandatory_skills' component score/coverage from 2 items
    # instead of the frozen 3.
    items = [item for item in GOLDEN_PROPOSAL_ITEMS if item["job_requirement_id"] != "req-m3"]
    with pytest.raises(ValueError):
        evaluate_candidate_scoreability(
            parse_gate_codes=[],
            proposal_gate_codes=[],
            proposal_items=items,
            requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
            effective_weights=GOLDEN_WEIGHTS,
            candidate_key="candidate-partial",
        )


def test_duplicate_job_requirement_id_in_proposal_items_raises_instead_of_double_counting():
    items = [dict(item) for item in GOLDEN_PROPOSAL_ITEMS] + [
        {"job_requirement_id": "req-m1", "state": "Matched"}
    ]
    with pytest.raises(ValueError):
        evaluate_candidate_scoreability(
            parse_gate_codes=[],
            proposal_gate_codes=[],
            proposal_items=items,
            requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
            effective_weights=GOLDEN_WEIGHTS,
            candidate_key="candidate-duplicate",
        )


def test_component_missing_from_effective_weights_raises_clear_value_error():
    weights = dict(GOLDEN_WEIGHTS)
    del weights[Component.DOMAIN_FIT]
    with pytest.raises(ValueError):
        evaluate_candidate_scoreability(
            parse_gate_codes=[],
            proposal_gate_codes=[],
            proposal_items=GOLDEN_PROPOSAL_ITEMS,
            requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
            effective_weights=weights,
            candidate_key="candidate-missing-weight",
        )


def test_empty_proposal_with_no_gate_codes_is_a_caller_contract_violation():
    with pytest.raises(ValueError):
        evaluate_candidate_scoreability(
            parse_gate_codes=[],
            proposal_gate_codes=[],
            proposal_items=[],
            requirement_components=GOLDEN_REQUIREMENT_COMPONENTS,
            effective_weights=GOLDEN_WEIGHTS,
            candidate_key="candidate-inconsistent",
        )


def test_criterion_state_from_label_rejects_unrecognized_label():
    with pytest.raises(ValueError):
        criterion_state_from_label("Rejected")


def test_criterion_state_from_label_accepts_all_four_frozen_labels():
    assert criterion_state_from_label("Matched") is CriterionState.MATCHED
    assert criterion_state_from_label("Partial") is CriterionState.PARTIAL
    assert criterion_state_from_label("Not Found") is CriterionState.NOT_FOUND
    assert criterion_state_from_label("Needs Validation") is CriterionState.NEEDS_VALIDATION


def test_component_score_of_empty_ratings_is_na_not_a_crash():
    assert component_score([]) is None


def test_coverage_weight_rejects_zero_requirement_count():
    with pytest.raises(ValueError):
        coverage_weight_bps(1000, 0)


def test_component_score_rejects_non_fraction_rating():
    with pytest.raises(TypeError):
        component_score([0.5])  # type: ignore[list-item]


# --- Exact tie-breaking and ranking (AR-30), ported from Story 1.5b --------


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


# --- Round-half-up display boundaries (AR-29), ported from Story 1.5b -----


def test_precise_score_percent_rounds_half_up_at_exact_boundary():
    total = Fraction(12_501, 2)  # 6250.5 bps = 62.505%
    assert precise_score_percent(total) == Decimal("62.51")


def test_headline_whole_percent_rounds_half_up_at_exact_boundary():
    total = Fraction(6250, 1)  # 62.50% exactly
    assert headline_whole_percent(total) == 63


def test_total_coverage_bps_excludes_needs_validation():
    weight = coverage_weight_bps(7000, 1)
    total = total_coverage_bps([(weight, CriterionState.NEEDS_VALIDATION)])
    assert total == Fraction(0, 1)
