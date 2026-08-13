"""Story 5.2, Task 1: pure `evidence_summary.summarize_evidence` fixtures."""

from __future__ import annotations

from src.domain.evidence_summary import EvidencePoint, summarize_evidence
from src.domain.scoring_configuration import Component

REQUIREMENT_TEXTS = {
    "r-mandatory-1": "Kubernetes operations",
    "r-mandatory-2": "Distributed systems design",
    "r-experience-1": "Incident leadership",
    "r-experience-2": "FinOps ownership",
    "r-responsibility-1": "On-call rotation",
    "r-preferred-1": "Terraform",
    "r-education-1": "BS Computer Science",
    "r-domain-1": "Fintech domain experience",
    "r-achievement-1": "Measurable reliability gains",
}

REQUIREMENT_COMPONENTS = {
    "r-mandatory-1": Component.MANDATORY_SKILLS,
    "r-mandatory-2": Component.MANDATORY_SKILLS,
    "r-experience-1": Component.RELEVANT_EXPERIENCE,
    "r-experience-2": Component.RELEVANT_EXPERIENCE,
    "r-responsibility-1": Component.RESPONSIBILITY_ALIGNMENT,
    "r-preferred-1": Component.PREFERRED_SKILLS_TOOLS,
    "r-education-1": Component.EDUCATION_CERTIFICATIONS,
    "r-domain-1": Component.DOMAIN_FIT,
    "r-achievement-1": Component.ACHIEVEMENT_EVIDENCE_QUALITY,
}


def test_full_mix_caps_at_four_strengths_three_gaps_in_frozen_component_order():
    items = [
        {"job_requirement_id": "r-achievement-1", "state": "Matched"},
        {"job_requirement_id": "r-domain-1", "state": "Matched"},
        {"job_requirement_id": "r-preferred-1", "state": "Partial"},
        {"job_requirement_id": "r-experience-1", "state": "Matched"},
        {"job_requirement_id": "r-mandatory-2", "state": "Matched"},
        {"job_requirement_id": "r-mandatory-1", "state": "Matched"},
        {"job_requirement_id": "r-responsibility-1", "state": "Not Found"},
        {"job_requirement_id": "r-experience-2", "state": "Needs Validation"},
        {"job_requirement_id": "r-education-1", "state": "Not Found"},
    ]

    summary = summarize_evidence(items, REQUIREMENT_TEXTS, REQUIREMENT_COMPONENTS)

    assert len(summary.strengths) == 4
    assert summary.strengths == [
        EvidencePoint(requirement_text="Kubernetes operations", state="Matched"),
        EvidencePoint(requirement_text="Distributed systems design", state="Matched"),
        EvidencePoint(requirement_text="Incident leadership", state="Matched"),
        EvidencePoint(requirement_text="Terraform", state="Partial"),
    ]
    assert len(summary.gaps) == 3
    assert summary.gaps == [
        EvidencePoint(requirement_text="FinOps ownership", state="Needs Validation"),
        EvidencePoint(requirement_text="On-call rotation", state="Not Found"),
        EvidencePoint(requirement_text="BS Computer Science", state="Not Found"),
    ]


def test_sparse_returns_fewer_than_cap_never_padded():
    items = [{"job_requirement_id": "r-mandatory-1", "state": "Matched"}]

    summary = summarize_evidence(items, REQUIREMENT_TEXTS, REQUIREMENT_COMPONENTS)

    assert summary.strengths == [EvidencePoint(requirement_text="Kubernetes operations", state="Matched")]
    assert summary.gaps == []


def test_empty_proposal_items_returns_empty_summary_not_an_error():
    summary = summarize_evidence([], REQUIREMENT_TEXTS, REQUIREMENT_COMPONENTS)

    assert summary.strengths == []
    assert summary.gaps == []


def test_not_found_and_needs_validation_stay_distinct_states():
    items = [
        {"job_requirement_id": "r-responsibility-1", "state": "Not Found"},
        {"job_requirement_id": "r-experience-2", "state": "Needs Validation"},
    ]

    summary = summarize_evidence(items, REQUIREMENT_TEXTS, REQUIREMENT_COMPONENTS)

    states = {point.state for point in summary.gaps}
    assert states == {"Not Found", "Needs Validation"}
