from __future__ import annotations

import pytest

from src.domain.question_set_projection import (
    COMPLETE,
    FAILED,
    GENERATING,
    NOT_GENERATED,
    RECOVERING,
    RETRYING,
    derive_question_set_state,
)


def test_no_job_is_not_generated():
    assert derive_question_set_state(None, 0, None) == NOT_GENERATED


def test_published_is_complete():
    assert derive_question_set_state("published", 0, None) == COMPLETE


def test_failed_is_failed():
    assert derive_question_set_state("failed", 0, "incomplete_proposal") == FAILED


def test_unrecoverable_is_failed():
    assert derive_question_set_state("unrecoverable", 0, "unstuck") == FAILED


def test_claimed_is_generating():
    assert derive_question_set_state("claimed", 0, None) == GENERATING


def test_completed_is_generating():
    assert derive_question_set_state("completed", 0, None) == GENERATING


def test_queued_fresh_is_generating():
    assert derive_question_set_state("queued", 0, None) == GENERATING


def test_queued_with_failure_reason_is_retrying():
    assert derive_question_set_state("queued", 0, "provider_unavailable") == RETRYING


def test_queued_with_reclaim_is_recovering():
    assert derive_question_set_state("queued", 1, None) == RECOVERING


def test_unrecognized_status_raises():
    with pytest.raises(ValueError):
        derive_question_set_state("bogus", 0, None)
