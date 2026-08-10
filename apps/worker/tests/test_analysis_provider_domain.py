"""Unit-level tests for the pure proposal schema and failure-category
mapping (AD-8/AD-17). No Docker/network required.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.analysis_provider import (
    AnalysisProposal,
    AnalysisState,
    FAILURE_CATEGORY_BY_REASON,
    FailureReason,
    JobRequirement,
    ProposalItem,
    map_failure,
    validate_complete,
)


def test_well_formed_proposal_validates():
    proposal = AnalysisProposal(
        items=[
            ProposalItem(job_requirement_id="JR-1", state=AnalysisState.MATCHED, locator="unit-1", excerpt="text"),
            ProposalItem(job_requirement_id="JR-2", state=AnalysisState.NOT_FOUND, locator=""),
        ]
    )
    assert proposal.items[0].state == AnalysisState.MATCHED


def test_malformed_state_rejected():
    with pytest.raises(ValidationError):
        AnalysisProposal(items=[{"job_requirement_id": "JR-1", "state": "Definitely Yes", "locator": ""}])


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        AnalysisProposal(
            items=[
                {
                    "job_requirement_id": "JR-1",
                    "state": "Matched",
                    "locator": "unit-1",
                    "score": 100,
                }
            ]
        )


def test_validate_complete_accepts_exact_coverage():
    requirements = [JobRequirement(id="JR-1", text="x"), JobRequirement(id="JR-2", text="y")]
    proposal = AnalysisProposal(
        items=[
            ProposalItem(job_requirement_id="JR-1", state=AnalysisState.MATCHED, locator="unit-1"),
            ProposalItem(job_requirement_id="JR-2", state=AnalysisState.NOT_FOUND, locator=""),
        ]
    )
    validate_complete(proposal, requirements)  # should not raise


def test_validate_complete_rejects_missing_requirement():
    requirements = [JobRequirement(id="JR-1", text="x"), JobRequirement(id="JR-2", text="y")]
    proposal = AnalysisProposal(
        items=[ProposalItem(job_requirement_id="JR-1", state=AnalysisState.MATCHED, locator="unit-1")]
    )
    with pytest.raises(ValueError, match="missing"):
        validate_complete(proposal, requirements)


def test_validate_complete_rejects_duplicate_requirement():
    requirements = [JobRequirement(id="JR-1", text="x")]
    proposal = AnalysisProposal(
        items=[
            ProposalItem(job_requirement_id="JR-1", state=AnalysisState.MATCHED, locator="unit-1"),
            ProposalItem(job_requirement_id="JR-1", state=AnalysisState.NOT_FOUND, locator=""),
        ]
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_complete(proposal, requirements)


def test_validate_complete_rejects_extra_requirement():
    requirements = [JobRequirement(id="JR-1", text="x")]
    proposal = AnalysisProposal(
        items=[
            ProposalItem(job_requirement_id="JR-1", state=AnalysisState.MATCHED, locator="unit-1"),
            ProposalItem(job_requirement_id="JR-99", state=AnalysisState.NOT_FOUND, locator=""),
        ]
    )
    with pytest.raises(ValueError, match="extra"):
        validate_complete(proposal, requirements)


def test_all_five_failure_categories_are_frozen_and_distinct():
    categories = set(FAILURE_CATEGORY_BY_REASON.values())
    assert categories == {
        "Analysis timed out",
        "Analysis service unavailable",
        "Analysis response could not be validated",
        "Automated analysis unavailable for this document",
        "Document processing interrupted",
    }
    assert len(FAILURE_CATEGORY_BY_REASON) == len(FailureReason)


@pytest.mark.parametrize("reason", list(FailureReason))
def test_map_failure_returns_exactly_one_frozen_category(reason):
    category = map_failure(reason)
    assert category in FAILURE_CATEGORY_BY_REASON.values()
