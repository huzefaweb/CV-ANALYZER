"""Pure tests for question_context.build_grounded_context (Story 7.1, AC#2) —
mirrors apps/gateway's test_evidence_detail.py fixture shape, minus
Component/ordering assertions (this module has no display-order concept)."""

from __future__ import annotations

from src.domain.question_context import GroundedRequirement, build_grounded_context

REQUIREMENT_TEXTS = {
    "r-mandatory-1": "Kubernetes operations",
    "r-experience-1": "Incident leadership",
}

PDF_LOCATOR = {"type": "pdf", "page": 2, "span": {"start": 10, "end": 40}, "excerpt": "led on-call rotation"}


def test_not_found_suppresses_locator_and_excerpt_even_if_present_in_source():
    items = [
        {
            "job_requirement_id": "r-mandatory-1",
            "state": "Not Found",
            # A malformed/fabricated upstream locator/excerpt must still be
            # suppressed for a Not Found state (AC#2's "never fabricated").
            "locator": "unit-pdf",
            "excerpt": "should never surface",
        }
    ]
    unit_locators = {"unit-pdf": PDF_LOCATOR}

    rows = build_grounded_context(items, REQUIREMENT_TEXTS, unit_locators)

    assert rows == [
        GroundedRequirement(
            job_requirement_id="r-mandatory-1",
            requirement_text="Kubernetes operations",
            state="Not Found",
            locator_description=None,
            excerpt="",
        )
    ]


def test_matched_item_carries_real_locator_description():
    items = [
        {
            "job_requirement_id": "r-experience-1",
            "state": "Matched",
            "locator": "unit-pdf",
            "excerpt": "led on-call rotation",
        }
    ]
    unit_locators = {"unit-pdf": PDF_LOCATOR}

    rows = build_grounded_context(items, REQUIREMENT_TEXTS, unit_locators)

    assert rows == [
        GroundedRequirement(
            job_requirement_id="r-experience-1",
            requirement_text="Incident leadership",
            state="Matched",
            locator_description="Page 2",
            excerpt="led on-call rotation",
        )
    ]


def test_empty_locator_on_non_not_found_item_is_defended_not_fabricated():
    items = [{"job_requirement_id": "r-experience-1", "state": "Partial", "locator": "", "excerpt": ""}]

    rows = build_grounded_context(items, REQUIREMENT_TEXTS, {})

    assert rows[0].locator_description is None
    assert rows[0].excerpt == ""
