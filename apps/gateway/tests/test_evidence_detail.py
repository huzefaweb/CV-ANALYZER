"""Story 6.2, Task 1: pure `evidence_detail.build_evidence_rows` fixtures."""

from __future__ import annotations

from src.domain.evidence_detail import EvidenceRow, build_evidence_rows
from src.domain.scoring_configuration import Component

REQUIREMENT_TEXTS = {
    "r-mandatory-1": "Kubernetes operations",
    "r-experience-1": "Incident leadership",
    "r-responsibility-1": "On-call rotation",
    "r-preferred-1": "Terraform",
}

REQUIREMENT_DISPLAY_IDS = {
    "r-mandatory-1": "REQ-1",
    "r-experience-1": "REQ-2",
    "r-responsibility-1": "REQ-3",
    "r-preferred-1": "REQ-4",
}

REQUIREMENT_COMPONENTS = {
    "r-mandatory-1": Component.MANDATORY_SKILLS,
    "r-experience-1": Component.RELEVANT_EXPERIENCE,
    "r-responsibility-1": Component.RESPONSIBILITY_ALIGNMENT,
    "r-preferred-1": Component.PREFERRED_SKILLS_TOOLS,
}

PDF_LOCATOR = {"type": "pdf", "page": 2, "span": {"start": 10, "end": 40}, "excerpt": "led on-call rotation"}
DOCX_LOCATOR = {"type": "docx", "path": "body/p[4]", "span": {"start": 0, "end": 20}, "excerpt": "Kubernetes"}


def test_full_mix_returns_every_row_uncapped_in_frozen_component_order():
    items = [
        {"job_requirement_id": "r-preferred-1", "state": "Partial", "locator": "", "excerpt": ""},
        {"job_requirement_id": "r-responsibility-1", "state": "Matched", "locator": "unit-pdf", "excerpt": "led on-call rotation"},
        {"job_requirement_id": "r-experience-1", "state": "Needs Validation", "locator": "", "excerpt": ""},
        {"job_requirement_id": "r-mandatory-1", "state": "Not Found", "locator": "", "excerpt": ""},
    ]
    unit_locators = {"unit-pdf": PDF_LOCATOR}

    rows = build_evidence_rows(items, REQUIREMENT_TEXTS, REQUIREMENT_DISPLAY_IDS, REQUIREMENT_COMPONENTS, unit_locators)

    assert len(rows) == 4
    assert rows == [
        EvidenceRow(
            requirement_display_id="REQ-1",
            requirement_text="Kubernetes operations",
            state="Not Found",
            locator_description=None,
            excerpt="",
            job_requirement_id="r-mandatory-1",
        ),
        EvidenceRow(
            requirement_display_id="REQ-2",
            requirement_text="Incident leadership",
            state="Needs Validation",
            locator_description=None,
            excerpt="",
            job_requirement_id="r-experience-1",
        ),
        EvidenceRow(
            requirement_display_id="REQ-3",
            requirement_text="On-call rotation",
            state="Matched",
            locator_description="Page 2",
            excerpt="led on-call rotation",
            job_requirement_id="r-responsibility-1",
        ),
        EvidenceRow(
            requirement_display_id="REQ-4",
            requirement_text="Terraform",
            state="Partial",
            locator_description=None,
            excerpt="",
            job_requirement_id="r-preferred-1",
        ),
    ]


def test_docx_locator_describes_as_stable_path():
    items = [{"job_requirement_id": "r-mandatory-1", "state": "Matched", "locator": "unit-docx", "excerpt": "Kubernetes"}]
    unit_locators = {"unit-docx": DOCX_LOCATOR}

    rows = build_evidence_rows(items, REQUIREMENT_TEXTS, REQUIREMENT_DISPLAY_IDS, REQUIREMENT_COMPONENTS, unit_locators)

    assert rows[0].locator_description == "body/p[4]"
    assert rows[0].excerpt == "Kubernetes"


def test_not_found_suppresses_a_populated_locator_and_excerpt_not_just_a_missing_one():
    # AC#1: "fabricated locators are absent" — even if the raw item is
    # malformed/legacy and carries a locator/excerpt on a Not Found
    # conclusion, this must never pass through to display.
    items = [{"job_requirement_id": "r-mandatory-1", "state": "Not Found", "locator": "unit-pdf", "excerpt": "should not appear"}]
    unit_locators = {"unit-pdf": PDF_LOCATOR}

    rows = build_evidence_rows(items, REQUIREMENT_TEXTS, REQUIREMENT_DISPLAY_IDS, REQUIREMENT_COMPONENTS, unit_locators)

    assert rows[0].locator_description is None
    assert rows[0].excerpt == ""


def test_empty_proposal_items_returns_empty_list_not_an_error():
    rows = build_evidence_rows([], REQUIREMENT_TEXTS, REQUIREMENT_DISPLAY_IDS, REQUIREMENT_COMPONENTS, {})

    assert rows == []


def test_unrecognized_locator_type_describes_as_none_without_raising():
    items = [{"job_requirement_id": "r-mandatory-1", "state": "Matched", "locator": "unit-x", "excerpt": "text"}]
    unit_locators = {"unit-x": {"type": "unknown"}}

    rows = build_evidence_rows(items, REQUIREMENT_TEXTS, REQUIREMENT_DISPLAY_IDS, REQUIREMENT_COMPONENTS, unit_locators)

    assert rows[0].locator_description is None
