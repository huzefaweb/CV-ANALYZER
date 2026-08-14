"""Story 5.3, Task 2: pure `retry_eligibility.check_retry_eligibility` fixtures."""

from __future__ import annotations

from src.domain.retry_eligibility import check_retry_eligibility


def test_eligible_when_frozen_inputs_failed_and_allowance_unused():
    assert check_retry_eligibility("frozen_inputs", "Failed", False) is True


def test_ineligible_when_session_not_frozen_inputs():
    assert check_retry_eligibility("preparing_to_start", "Failed", False) is False


def test_ineligible_when_outcome_needs_review():
    assert check_retry_eligibility("frozen_inputs", "NeedsReview", False) is False


def test_ineligible_when_outcome_new_result():
    assert check_retry_eligibility("frozen_inputs", "NewResult", False) is False


def test_ineligible_when_outcome_reused_result():
    assert check_retry_eligibility("frozen_inputs", "ReusedResult", False) is False


def test_ineligible_when_allowance_already_consumed():
    assert check_retry_eligibility("frozen_inputs", "Failed", True) is False
