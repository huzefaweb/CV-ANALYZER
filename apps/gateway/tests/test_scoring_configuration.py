from fractions import Fraction

import pytest

from src.domain.scoring_configuration import (
    Component,
    applicable_components,
    effective_weights_bps,
)


def test_all_seven_applicable_returns_base_weights():
    applicable = {c: True for c in Component}
    weights = effective_weights_bps(applicable)
    assert weights[Component.MANDATORY_SKILLS] == 3000
    assert weights[Component.RELEVANT_EXPERIENCE] == 2500
    assert weights[Component.RESPONSIBILITY_ALIGNMENT] == 1500
    assert weights[Component.PREFERRED_SKILLS_TOOLS] == 1000
    assert weights[Component.EDUCATION_CERTIFICATIONS] == 1000
    assert weights[Component.DOMAIN_FIT] == 500
    assert weights[Component.ACHIEVEMENT_EVIDENCE_QUALITY] == 500
    assert sum(weights.values()) == 10_000


def test_empty_component_redistributes_proportionally_and_reconciles():
    applicable = {c: True for c in Component}
    applicable[Component.EDUCATION_CERTIFICATIONS] = False
    weights = effective_weights_bps(applicable)
    assert Component.EDUCATION_CERTIFICATIONS not in weights
    assert sum(weights.values()) == 10_000
    # Redistribution is proportional to base weight, largest-remainder tie-break.
    assert weights[Component.MANDATORY_SKILLS] > 3000


def test_zero_applicable_raises():
    applicable = {c: False for c in Component}
    with pytest.raises(ValueError):
        effective_weights_bps(applicable)


def test_missing_entries_raises():
    applicable = {Component.MANDATORY_SKILLS: True}
    with pytest.raises(ValueError):
        effective_weights_bps(applicable)


def test_applicable_components_from_requirement_component_set():
    present = {"mandatory_skills", "domain_fit"}
    result = applicable_components(present)
    assert result[Component.MANDATORY_SKILLS] is True
    assert result[Component.DOMAIN_FIT] is True
    assert result[Component.RELEVANT_EXPERIENCE] is False
