from __future__ import annotations

import pytest

from src.domain.print_projection import (
    REPORT_ONLY,
    SCORED_COMBINED,
    derive_print_scope,
    derive_trigger,
    is_print_blocked,
)


def test_ranked_scope_is_scored_combined():
    assert derive_print_scope("Ranked") == SCORED_COMBINED


def test_needs_review_scope_is_report_only():
    assert derive_print_scope("NeedsReview") == REPORT_ONLY


def test_unrecognized_outcome_raises():
    with pytest.raises(ValueError):
        derive_print_scope("Failed")


@pytest.mark.parametrize(
    "question_set_state,expected",
    [
        ("NotGenerated", True),
        ("Generating", True),
        ("Recovering", True),
        ("Retrying", True),
        ("Failed", True),
        ("Complete", False),
    ],
)
def test_ranked_blocked_unless_complete(question_set_state, expected):
    assert is_print_blocked("Ranked", question_set_state) is expected


@pytest.mark.parametrize(
    "question_set_state",
    ["NotGenerated", "Generating", "Recovering", "Retrying", "Complete", "Failed"],
)
def test_needs_review_never_blocked(question_set_state):
    assert is_print_blocked("NeedsReview", question_set_state) is False


def test_revision_one_trigger_is_initial_analysis():
    assert derive_trigger(1, None) == "Initial analysis"


def test_retry_revision_trigger_names_document():
    assert derive_trigger(2, "A7K2") == "Retry of Document A7K2"


def test_retry_revision_with_missing_reference_falls_back():
    assert derive_trigger(2, None) == "Retry"
