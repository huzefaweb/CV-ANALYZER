"""Story 7.2 (AC#1): question_set_completeness.validate_complete_set."""

from __future__ import annotations

import pytest

from src.domain.question_set_completeness import validate_complete_set

_CATEGORIES = [
    "technical_functional",
    "experience_verification",
    "gap_focused",
    "behavioral",
    "follow_up",
]


def _valid_items() -> list[dict]:
    # Ten items, each of the five categories appearing twice.
    return [
        {"number": i + 1, "category": _CATEGORIES[i % 5], "text": f"Question {i + 1}?"}
        for i in range(10)
    ]


def test_valid_ten_item_five_category_set_passes():
    validate_complete_set(_valid_items())


def test_wrong_count_is_rejected():
    with pytest.raises(ValueError):
        validate_complete_set(_valid_items()[:9])


def test_missing_category_is_rejected():
    items = _valid_items()
    # Replace every follow_up item with technical_functional, removing the
    # follow_up category from the set entirely.
    for item in items:
        if item["category"] == "follow_up":
            item["category"] = "technical_functional"
    with pytest.raises(ValueError):
        validate_complete_set(items)


def test_duplicate_numbers_are_rejected():
    items = _valid_items()
    items[1]["number"] = items[0]["number"]
    with pytest.raises(ValueError):
        validate_complete_set(items)


def test_empty_text_is_rejected():
    items = _valid_items()
    items[0]["text"] = "   "
    with pytest.raises(ValueError):
        validate_complete_set(items)
