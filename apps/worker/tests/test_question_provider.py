"""Pure tests for question_provider.py's structural proposal-shape validator
(Story 7.1, AC#2)."""

from __future__ import annotations

import pytest

from src.domain.question_provider import (
    QuestionCategory,
    QuestionItem,
    QuestionProposal,
    QUESTION_SET_SIZE,
    validate_question_shape,
)


def _item(number: int, source_requirement_id: str = "JR-1") -> QuestionItem:
    return QuestionItem(
        number=number,
        category=QuestionCategory.TECHNICAL_FUNCTIONAL,
        text=f"Question {number}?",
        source_requirement_id=source_requirement_id,
    )


def test_valid_ten_item_proposal_passes():
    proposal = QuestionProposal(items=[_item(n) for n in range(1, QUESTION_SET_SIZE + 1)])
    validate_question_shape(proposal)  # does not raise


def test_wrong_count_is_rejected():
    # Field(min_length=QUESTION_SET_SIZE, max_length=QUESTION_SET_SIZE) on
    # QuestionProposal.items rejects an off-count list at construction time
    # (schema-level constraint, so Ollama's grammar-constrained structured
    # output is guided toward exactly 10 items) — validate_question_shape's
    # own count check is unreachable for a proposal that already failed to
    # construct, so this proves the earlier of the two guards instead.
    with pytest.raises(ValueError):
        QuestionProposal(items=[_item(n) for n in range(1, QUESTION_SET_SIZE)])  # 9 items


def test_duplicate_numbers_are_rejected():
    items = [_item(n) for n in range(1, QUESTION_SET_SIZE)] + [_item(1)]  # 1 repeated, 10 missing
    proposal = QuestionProposal(items=items)
    with pytest.raises(ValueError):
        validate_question_shape(proposal)


def test_missing_number_is_rejected():
    items = [_item(n) for n in range(2, QUESTION_SET_SIZE + 2)]  # 2..11, missing 1
    proposal = QuestionProposal(items=items)
    with pytest.raises(ValueError):
        validate_question_shape(proposal)


def test_empty_source_requirement_id_allowed_for_gap_focused_question():
    items = [_item(n) for n in range(1, QUESTION_SET_SIZE + 1)]
    items[0] = _item(1, source_requirement_id="")
    proposal = QuestionProposal(items=items)
    validate_question_shape(proposal)  # does not raise
