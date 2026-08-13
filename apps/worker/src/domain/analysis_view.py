"""Domain-kernel analysis-view builder and provider-request budget gate
(Story 4.3; AD-9, AR-21-23, FR-9/11/12).

Builds the minimized, normalized, protected/proxy-filtered view the
provider is allowed to see, from an already-persisted parse artifact's
`ResumeSourceUnit` list — never `candidate_identities`, which never reaches
this module or any descendant (AD-9's central invariant). Also decides, via
a deterministic AF-13-pinned token budget, whether the complete payload
fits one provider call; if it does not, no provider call is made and this
module returns overflow provenance instead (Story 4.4 is the first caller
that acts on either result — this module only decides).

No HTTP client, provider SDK, or DB driver import belongs here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .analysis_provider import JobRequirement, ResumeSourceUnit
from .identity_extraction import EMAIL_PATTERN, PHONE_PATTERN
from .parse_gates import HEADING_KEYWORDS, normalize_heading_text

# Section headings that mark the start of protected/irrelevant content
# (AD-9) rather than a job-related content class. Every unit from an
# excluded heading until the next heading (allowed or excluded) is dropped
# from the analysis view entirely.
EXCLUDED_HEADING_KEYWORDS = frozenset(
    {
        "personal information",
        "personal details",
        "demographics",
        "about me",
        "declaration",
        "references",
    }
)

# Deterministic, non-AI protected-content line patterns (AD-9), checked
# regardless of section — so a stray protected line inside an otherwise
# allowed section, or before the first heading (which parse_gates.classify_
# blocks defaults to "employment"), is still caught.
# ponytail: regex/keyword heuristics only, matching identity_extraction.py's
# EMAIL_PATTERN/PHONE_PATTERN convention — not exhaustive NLP-grade
# protected-attribute detection. Both directions are a known, bounded V1
# ceiling: false negatives on subtle phrasing (backstopped by the
# already-proven provider-level invariance test — test_ollama_analysis.py::
# test_protected_proxy_variant_invariance, Story 1.3 — which shows a leaked
# marker still doesn't change conclusions) and false positives on
# job-related text that happens to share vocabulary with a pattern (e.g. a
# "15-year-old legacy system"), which costs Evidence completeness, not a
# protected-content leak. MARITAL_PATTERN/GENDER_PATTERN/NATIONALITY_PATTERN
# are deliberately restricted to explicit label form ("marital status:",
# "gender:", "nationality:") rather than bare keywords, to avoid nuking
# common job-related phrases ("single page application", "single-handedly")
# that share vocabulary with the protected term. Upgrade only if a real
# leak or an unacceptable false-positive rate surfaces in an AF-8 fixture.
AGE_PATTERN = re.compile(r"\b\d{1,3}\s*(?:years?[\s-]*old|y/?o)\b", re.IGNORECASE)
DOB_PATTERN = re.compile(r"\b(?:date of birth|dob|born on|born in)\b", re.IGNORECASE)
GENDER_PATTERN = re.compile(r"\b(?:gender|sex)\s*:\s*\S", re.IGNORECASE)
MARITAL_PATTERN = re.compile(r"\bmarital status\s*:\s*\S", re.IGNORECASE)
NATIONALITY_PATTERN = re.compile(r"\b(?:nationality|citizenship)\s*:\s*\S", re.IGNORECASE)
# Requires a 2-letter state-code immediately before the digits ("... IL
# 62704") rather than matching any bare 5-digit number, which would
# otherwise false-positive on ticket counts, user counts, GPA-adjacent
# figures, etc. Narrower than a full address parser, but STREET_ADDRESS_
# PATTERN below independently catches the common "<number> <street name>
# <suffix>" case even without a trailing state+zip.
ZIP_PATTERN = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
STREET_ADDRESS_PATTERN = re.compile(
    r"\b\d+\s+\w+(?:\s\w+){0,3}\s+"
    r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|blvd|boulevard)\b",
    re.IGNORECASE,
)

PROTECTED_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    EMAIL_PATTERN,
    PHONE_PATTERN,
    AGE_PATTERN,
    DOB_PATTERN,
    GENDER_PATTERN,
    MARITAL_PATTERN,
    NATIONALITY_PATTERN,
    ZIP_PATTERN,
    STREET_ADDRESS_PATTERN,
)


def _is_excluded_heading(text: str) -> bool:
    lowered = normalize_heading_text(text)
    return lowered is not None and lowered in EXCLUDED_HEADING_KEYWORDS


def _is_allowed_heading(text: str) -> bool:
    lowered = normalize_heading_text(text)
    if lowered is None:
        return False
    return any(lowered in keywords for keywords in HEADING_KEYWORDS.values())


def _is_protected_line(text: str) -> bool:
    return any(pattern.search(text) for pattern in PROTECTED_LINE_PATTERNS)


def build_analysis_view(units: list[ResumeSourceUnit]) -> list[ResumeSourceUnit]:
    """AC#1/AR-21/AR-22: the permitted, locator-preserving subset of
    already-parsed units. Protected/proxy/identity content is excluded by
    section heading or line-level pattern; every surviving unit is returned
    unchanged (same id/text/locator — "retains stable source locators").

    The first unit is unconditionally excluded unless it is itself a
    recognized section heading — deliberately not gated on
    `identity_extraction.extract_name_candidate`'s narrower name heuristic
    (an oversized or otherwise-unrecognized name-only header line could
    slip past that heuristic's own rejection checks and leak into the
    provider-bound view). AD-9's exclusion of name/identity content is the
    single highest-priority invariant in this module; a resume without any
    header line loses one line of Evidence in the rare case its first unit
    is genuine job-related content with no identity line at all, which is a
    far smaller cost than any residual identity-leak risk."""
    permitted: list[ResumeSourceUnit] = []
    in_excluded_section = False
    for index, unit in enumerate(units):
        stripped = unit.text.strip()
        if _is_excluded_heading(stripped):
            in_excluded_section = True
            continue
        if _is_allowed_heading(stripped):
            in_excluded_section = False
            continue
        if index == 0 and stripped:
            continue
        if in_excluded_section or _is_protected_line(stripped):
            continue
        permitted.append(unit)
    return permitted


# AF-13-pinned (_bmad-output/implementation-artifacts/af13-analysis-provider-
# evidence.md): Ollama's OLLAMA_CONTEXT_LENGTH for the V1-default
# qwen2.5:0.5b-instruct deployment (per AD-22).
CONTEXT_LENGTH_TOKENS = 4096
# Fixed versioned reservations (AD-9) — frozen for this pipeline version,
# bumped only if ollama_analysis.py's system prompt/schema materially grows.
INSTRUCTION_RESERVE_TOKENS = 400
SCHEMA_RESERVE_TOKENS = 300
SAFETY_MARGIN_TOKENS = 200


def estimate_tokens(text: str) -> int:
    """Deterministic conservative token estimate. AF-13 pins the active
    adapter's context-length limit but records no installable Python
    tokenizer for qwen2.5 (tiktoken is OpenAI-model-specific).
    ponytail: chars/4 ceiling, not a real BPE tokenizer — deterministic and
    conservative (rounds up) is what AD-9/AR-23 require; swap for a pinned
    tokenizer library only if the active adapter switches to one that ships
    an exact Python tokenizer (e.g. tiktoken for an Azure OpenAI model)."""
    return -(-len(text) // 4)


@dataclass(frozen=True)
class BudgetOverflow:
    """AC#3: internal-only overflow provenance — never a new public reason
    code. Story 4.4 (not this module) is expected to stage this as the
    existing GateCode.COVERAGE_BELOW_7000_BPS (parse_gates.py) with zero
    evaluated coverage; this dataclass only carries the numbers behind that
    decision, it does not construct or reference a GateCode itself.

    `resume_budget_tokens` can be zero or negative when the frozen Job
    Requirements alone consume the whole context budget — that is still a
    real overflow (even an empty resume wouldn't fit), not a bug in the
    subtraction; the field name reflects what's being compared, not which
    side (resume vs. requirements) caused it."""

    resume_tokens: int
    resume_budget_tokens: int


def check_budget(requirements: list[JobRequirement], permitted_units: list[ResumeSourceUnit]) -> BudgetOverflow | None:
    """AC#2/#3: None if the complete payload fits one provider call (AR-23:
    no truncation/sampling/dropped units ever follows — the caller may pass
    `permitted_units` to `propose()` unchanged); otherwise the overflow
    provenance to stage instead of a provider call. Mirrors
    `ollama_analysis._build_user_message`'s actual `"- {id}: {text}"` and
    `"[{id}] {text}"` formatting so the estimate reflects what will actually
    be sent (the fixed structural wrapper text around them is covered by
    `SAFETY_MARGIN_TOKENS`)."""
    requirements_tokens = sum(estimate_tokens(f"- {r.id}: {r.text}") for r in requirements)
    resume_budget_tokens = (
        CONTEXT_LENGTH_TOKENS
        - INSTRUCTION_RESERVE_TOKENS
        - SCHEMA_RESERVE_TOKENS
        - SAFETY_MARGIN_TOKENS
        - requirements_tokens
    )
    resume_tokens = sum(estimate_tokens(f"[{u.id}] {u.text}") for u in permitted_units)
    if resume_tokens > resume_budget_tokens:
        return BudgetOverflow(resume_tokens=resume_tokens, resume_budget_tokens=resume_budget_tokens)
    return None


__all__ = [
    "EXCLUDED_HEADING_KEYWORDS",
    "PROTECTED_LINE_PATTERNS",
    "build_analysis_view",
    "CONTEXT_LENGTH_TOKENS",
    "INSTRUCTION_RESERVE_TOKENS",
    "SCHEMA_RESERVE_TOKENS",
    "SAFETY_MARGIN_TOKENS",
    "estimate_tokens",
    "BudgetOverflow",
    "check_budget",
]
