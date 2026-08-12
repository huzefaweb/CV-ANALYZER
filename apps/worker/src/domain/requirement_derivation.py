"""Pure Job-Requirement-derivation proposal contract (AD-4): the worker
derives Job Requirements from the Job Description text only — no Resume.

Mirrors analysis_provider.py's shape/failure-mapping convention exactly;
reuses its FailureReason/FAILURE_CATEGORY_BY_REASON/map_failure verbatim
(the five frozen public failure categories are provider-transport-generic,
not Resume-analysis-specific). No HTTP client, provider SDK, or DB driver
import belongs here.

REQUIREMENT_DERIVATION_FUNCTIONS documents the module-level function
contract every adapter module must expose with matching name/arity,
enforced by tests/test_requirement_derivation_port.py — mirrors
ANALYSIS_PROVIDER_FUNCTIONS's own port-contract-test pattern.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .analysis_provider import FAILURE_CATEGORY_BY_REASON, AnalysisProviderError, FailureReason, map_failure

REQUIREMENT_DERIVATION_FUNCTIONS = ("derive_requirements",)

# The seven frozen rubric components (must string-match
# apps/gateway/src/domain/scoring_configuration.py's Component enum values).
ALLOWED_COMPONENTS = frozenset(
    {
        "mandatory_skills",
        "relevant_experience",
        "responsibility_alignment",
        "preferred_skills_tools",
        "education_certifications",
        "domain_fit",
        "achievement_evidence_quality",
    }
)
ALLOWED_CLASSIFICATIONS = frozenset({"mandatory", "preferred"})


class ProposedRequirementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str
    classification: str
    text: str
    source_start: int
    source_end: int


class RequirementProposal(BaseModel):
    """AD-4: a versioned proposal of Job Requirements derived from the
    Job Description only — no score/rank/decision."""

    model_config = ConfigDict(extra="forbid")

    items: list[ProposedRequirementItem]


def validate_schema(proposal: RequirementProposal, job_description_text: str) -> None:
    """Raise ValueError unless every item names an allowed component/
    classification and its locator is within the submitted text's bounds."""
    text_length = len(job_description_text)
    for item in proposal.items:
        if item.component not in ALLOWED_COMPONENTS:
            raise ValueError(f"unsupported component: {item.component!r}")
        if item.classification not in ALLOWED_CLASSIFICATIONS:
            raise ValueError(f"unsupported classification: {item.classification!r}")
        if not (0 <= item.source_start < item.source_end <= text_length):
            raise ValueError(
                f"locator out of bounds: [{item.source_start}, {item.source_end}) "
                f"for text of length {text_length}"
            )


__all__ = [
    "REQUIREMENT_DERIVATION_FUNCTIONS",
    "ALLOWED_COMPONENTS",
    "ALLOWED_CLASSIFICATIONS",
    "ProposedRequirementItem",
    "RequirementProposal",
    "validate_schema",
    "AnalysisProviderError",
    "FailureReason",
    "FAILURE_CATEGORY_BY_REASON",
    "map_failure",
]
