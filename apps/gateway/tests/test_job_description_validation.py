"""Story 3.1: pure boundary tests for the AF-2 content fixture — the frozen
≥200-non-whitespace-character rule (TRACEABILITY.md: "Job Description
validation | ≥200 non-whitespace characters and frozen validation rules |
AF-2 fixtures"). No DATABASE_URL needed — this is a pure function.
"""

from __future__ import annotations

from src.adapters.new_analysis import MINIMUM_NON_WHITESPACE_CHARACTERS, _validate_job_description


def test_below_threshold_is_invalid():
    result = _validate_job_description("a" * 199)
    assert result["non_whitespace_count"] == 199
    assert result["is_valid"] is False
    assert result["minimum_required"] == MINIMUM_NON_WHITESPACE_CHARACTERS


def test_exactly_at_threshold_is_valid():
    result = _validate_job_description("a" * 200)
    assert result["non_whitespace_count"] == 200
    assert result["is_valid"] is True


def test_whitespace_padding_does_not_count_toward_threshold():
    # 250 characters, all whitespace: 0 non-whitespace, must stay invalid.
    result = _validate_job_description(" \n\t" * 84)
    assert result["non_whitespace_count"] == 0
    assert result["is_valid"] is False


def test_multiline_content_is_counted_not_rejected():
    # AC#1: multiline content must be retained/counted, not penalized.
    paragraph = "Senior Engineer role.\n\nRequirements:\n" + ("x" * 180)
    result = _validate_job_description(paragraph)
    assert result["non_whitespace_count"] >= 200
    assert result["is_valid"] is True
