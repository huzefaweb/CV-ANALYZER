from __future__ import annotations

import pytest

from src.domain.progress_projection import (
    ANALYZING,
    FAILED,
    NEEDS_REVIEW,
    PARSING,
    QUEUED,
    RECOVERING,
    RETRYING,
    SUCCEEDED,
    derive_row_state,
)


def test_finalized_new_result_is_succeeded():
    assert derive_row_state("finalized", 0, None, "NewResult") == SUCCEEDED


def test_finalized_reused_result_is_succeeded():
    # Story 5.3: a retry-revision's carried-forward, previously-ranked
    # Candidate is written as "ReusedResult", not "NewResult" — it must
    # display identically to a fresh success.
    assert derive_row_state("finalized", 0, None, "ReusedResult") == SUCCEEDED


def test_finalized_needs_review():
    assert derive_row_state("finalized", 0, None, "NeedsReview") == NEEDS_REVIEW


def test_finalized_failed():
    assert derive_row_state("finalized", 0, "Analysis timed out", "Failed") == FAILED


def test_finalized_with_non_terminal_outcome_raises():
    with pytest.raises(ValueError):
        derive_row_state("finalized", 0, None, "queued")


def test_exhausted_failed_before_finalize():
    assert derive_row_state("failed", 0, "Analysis timed out", "queued") == FAILED


def test_claimed_is_parsing():
    assert derive_row_state("claimed", 0, None, "queued") == PARSING


def test_parsed_is_analyzing():
    assert derive_row_state("parsed", 0, None, "queued") == ANALYZING


def test_completed_is_analyzing():
    assert derive_row_state("completed", 0, None, "queued") == ANALYZING


def test_fresh_queued():
    assert derive_row_state("queued", 0, None, "queued") == QUEUED


def test_queued_with_failure_reason_is_retrying():
    assert derive_row_state("queued", 0, "lease_exhausted", "queued") == RETRYING


def test_queued_with_reclaim_is_recovering():
    assert derive_row_state("queued", 1, None, "queued") == RECOVERING


def test_unrecognized_status_raises():
    with pytest.raises(ValueError):
        derive_row_state("bogus", 0, None, "queued")


def test_reclaim_never_regresses_to_bare_queued():
    """A mid-attempt reclaim resets status back to 'queued' at the DB layer
    (recovery_sweep.py's own field mutation), but the derived public state
    must never be bare "Queued" once reclaim_count > 0 or failure_reason is
    set — that would look like the row restarted from scratch to the
    client."""
    for reclaim_count in (1, 2, 5):
        assert derive_row_state("queued", reclaim_count, None, "queued") != QUEUED
    assert derive_row_state("queued", 0, "lease_exhausted", "queued") != QUEUED
