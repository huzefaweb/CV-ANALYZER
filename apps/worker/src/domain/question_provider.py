"""Pure Interview Question Set proposal contract (Story 7.1; AR-34, AR-40).

Mirrors `analysis_provider.py`'s shape: every provider adapter (Ollama or
Azure OpenAI) produces this same structured `QuestionProposal` or raises
`AnalysisProviderError` (imported, not redefined — the five frozen public
failure categories are provider-call failures generically, not
analysis-specific).

`QUESTION_PROVIDER_FUNCTIONS` documents the module-level function contract
every adapter module must expose with matching name/arity, enforced by
tests/test_question_provider_port.py — mirrors `ANALYSIS_PROVIDER_FUNCTIONS`'s
identical convention.

No HTTP client, provider SDK, or DB driver import belongs here.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .analysis_provider import AnalysisProviderError, FailureReason, map_failure  # noqa: F401 - re-exported
from .question_context import GroundedRequirement

QUESTION_PROVIDER_FUNCTIONS = ("propose_questions",)

QUESTION_SET_SIZE = 10


class QuestionCategory(str, Enum):
    TECHNICAL_FUNCTIONAL = "technical_functional"
    EXPERIENCE_VERIFICATION = "experience_verification"
    GAP_FOCUSED = "gap_focused"
    BEHAVIORAL = "behavioral"
    FOLLOW_UP = "follow_up"


class QuestionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    category: QuestionCategory
    text: str
    # The grounding Job Requirement's raw id, or "" for a genuinely
    # gap-focused question with no single matching requirement (mirrors
    # ProposalItem.locator's empty-string-for-Not-Found convention).
    source_requirement_id: str = ""


class QuestionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # min_length/max_length=10 (not just validate_question_shape's runtime
    # check) so the JSON schema handed to the provider's grammar-constrained
    # structured-output decoding itself encodes the fixed target count —
    # AnalysisProposal.items has no such bound because its correct count
    # varies per request (one item per requirement); QuestionProposal's is a
    # frozen constant, the right case for a schema-level constraint.
    items: list[QuestionItem] = Field(min_length=QUESTION_SET_SIZE, max_length=QUESTION_SET_SIZE)


def validate_question_shape(proposal: QuestionProposal) -> None:
    """Raise ValueError unless there are exactly QUESTION_SET_SIZE items with
    unique numbers covering 1..QUESTION_SET_SIZE exactly once.

    This is a structural sanity check before staging (mirrors
    `validate_complete`'s role in `main.py::_process_one_candidate`) — it is
    NOT Story 7.2's coordinator-level "validated complete ten-question set"
    acceptance gate (category-coverage/source-linkage completeness), which
    stays Story 7.2's scope."""
    numbers = [item.number for item in proposal.items]
    if len(numbers) != QUESTION_SET_SIZE:
        raise ValueError(
            f"proposal must contain exactly {QUESTION_SET_SIZE} items, got {len(numbers)}"
        )
    if set(numbers) != set(range(1, QUESTION_SET_SIZE + 1)):
        raise ValueError(
            f"proposal item numbers must be exactly 1..{QUESTION_SET_SIZE}, got {sorted(numbers)}"
        )


def validate_question_grounding(proposal: QuestionProposal, grounded: list[GroundedRequirement]) -> None:
    """Code review addition (Blind Hunter, High — AF-9/NFR-1): mirrors
    `validate_locators`'s role on the Candidate-analysis path — rejects a
    proposal that claims a `source_requirement_id` naming a Job Requirement
    the provider was never given (an invented/hallucinated reference), the
    one grounding check `validate_question_shape` (count/numbering only)
    does not perform. An empty `source_requirement_id` legitimately means
    "gap-focused, no single matching requirement" and is never rejected."""
    permitted_ids = {g.job_requirement_id for g in grounded}
    for item in proposal.items:
        if item.source_requirement_id and item.source_requirement_id not in permitted_ids:
            raise ValueError(
                f"source_requirement_id {item.source_requirement_id!r} does not name a permitted grounded requirement"
            )
