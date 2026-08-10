"""Pure analysis-provider proposal contract and failure-category mapping
(AD-8/AD-9/AD-17/AD-22).

Every provider adapter — Ollama or Azure OpenAI — produces this same
structured AnalysisProposal shape or raises AnalysisProviderError mapped to
one of the five frozen public failure categories. No HTTP client, provider
SDK, or DB driver import belongs here; this is the shape and classification
rule descendant code depends on, not how the provider was called.

ANALYSIS_PROVIDER_FUNCTIONS documents the module-level function contract
every adapter module (`ollama_analysis`, `azure_openai_analysis`) must
expose with matching name/arity, enforced by tests/test_analysis_provider_port.py
— mirrors gateway's IDENTITY_PORT_FUNCTIONS convention (src/domain/identity.py
in apps/gateway) for the same reason: adapters are modules here, not classes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict

ANALYSIS_PROVIDER_FUNCTIONS = ("propose",)


class AnalysisState(str, Enum):
    MATCHED = "Matched"
    PARTIAL = "Partial"
    NOT_FOUND = "Not Found"
    NEEDS_VALIDATION = "Needs Validation"


@dataclass(frozen=True)
class JobRequirement:
    id: str
    text: str


@dataclass(frozen=True)
class ResumeSourceUnit:
    """A minimized, normalized Resume unit (AD-9) — never identity/contact/protected content."""

    id: str
    text: str


class ProposalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_requirement_id: str
    state: AnalysisState
    locator: str
    excerpt: str = ""


class AnalysisProposal(BaseModel):
    """AD-8: exactly one allowed state per requested Job Requirement, no score/rank/decision."""

    model_config = ConfigDict(extra="forbid")

    items: list[ProposalItem]


def validate_complete(proposal: AnalysisProposal, requirements: list[JobRequirement]) -> None:
    """Raise ValueError unless the proposal covers every requested requirement exactly once."""
    expected_ids = {r.id for r in requirements}
    seen_ids = [item.job_requirement_id for item in proposal.items]
    if len(seen_ids) != len(set(seen_ids)):
        raise ValueError("proposal contains duplicate job_requirement_id entries")
    if set(seen_ids) != expected_ids:
        raise ValueError(
            f"proposal does not cover exactly the requested requirements: "
            f"missing={expected_ids - set(seen_ids)} extra={set(seen_ids) - expected_ids}"
        )


class FailureReason(str, Enum):
    """Adapter-internal classification, translated from provider/transport specifics."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    REFUSED = "refused"
    INTERRUPTED = "interrupted"


# The five frozen public failure categories (SOLUTION-DESIGN.md #12) — no
# raw provider/model/HTTP detail may substitute for or extend these.
FAILURE_CATEGORY_BY_REASON: dict[FailureReason, str] = {
    FailureReason.TIMEOUT: "Analysis timed out",
    FailureReason.UNAVAILABLE: "Analysis service unavailable",
    FailureReason.MALFORMED: "Analysis response could not be validated",
    FailureReason.REFUSED: "Automated analysis unavailable for this document",
    FailureReason.INTERRUPTED: "Document processing interrupted",
}


def map_failure(reason: FailureReason) -> str:
    return FAILURE_CATEGORY_BY_REASON[reason]


class AnalysisProviderError(Exception):
    """Raised by adapters instead of leaking raw provider/transport exceptions."""

    def __init__(self, reason: FailureReason):
        self.reason = reason
        self.category = map_failure(reason)
        super().__init__(self.category)
