"""Deterministic parser gates (AF-5) — pure functions, no parser SDK or I/O
imports. Enforced by tests/test_domain_boundary.py.

Frozen public reason codes this module can produce (SOLUTION-DESIGN.md §12 /
acceptance-fixtures.md AF-5):
  PARSE_FATAL              - parser could not produce reliable normalized text at all
  TEXT_BELOW_500           - fewer than 500 normalized non-whitespace characters
  COHERENT_BLOCKS_BELOW_2  - fewer than 2 coherent content blocks
  COVERAGE_BELOW_7000_BPS  - evaluated coverage below 7,000 effective basis points

`COVERAGE_BELOW_7000_BPS` has two producers sharing one Recruiter-visible
meaning (AD-9/AD-11/AR-26): Story 4.3's pre-provider-call token-budget
overflow (`apps/worker/src/domain/analysis_view.check_budget`) is the first;
post-provider criterion-coverage scoring (Story 4.5+) is the second. Neither
producer lives in this module — this enum is only the shared vocabulary.
AI output cannot set, clear, or override any of these codes; they are derived
here purely from parser-delineated structure or deterministic budget math.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .analysis_provider import ResumeSourceUnit

TEXT_MIN_CHARS = 500
BLOCK_MIN_CHARS = 100
MIN_COHERENT_BLOCKS = 2
MAX_REPLACEMENT_RATIO = 0.20

ALLOWED_BLOCK_CLASSES = frozenset(
    {"employment", "education", "skills", "projects", "certifications", "achievements"}
)


class GateCode(str, Enum):
    PARSE_FATAL = "PARSE_FATAL"
    TEXT_BELOW_500 = "TEXT_BELOW_500"
    COHERENT_BLOCKS_BELOW_2 = "COHERENT_BLOCKS_BELOW_2"
    COVERAGE_BELOW_7000_BPS = "COVERAGE_BELOW_7000_BPS"


class ParseFatalError(Exception):
    """Raised by adapters (never by this module) when a parser cannot produce
    reliable normalized text at all — maps to GateCode.PARSE_FATAL."""


@dataclass(frozen=True)
class Block:
    """A parser-delineated content block already assigned a content class and
    already run through deterministic repeated header/footer removal."""

    text: str
    content_class: str


def count_normalized_non_whitespace_chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def replacement_char_ratio(text: str) -> float:
    """Fraction of characters that are the Unicode replacement character or a
    control character (excluding common whitespace control chars)."""
    if not text:
        return 0.0
    bad = sum(
        1
        for ch in text
        if ch == "�" or (ord(ch) < 0x20 and ch not in "\t\n\r") or 0x7F <= ord(ch) < 0xA0
    )
    return bad / len(text)


def is_coherent_block(block: Block) -> bool:
    return (
        block.content_class in ALLOWED_BLOCK_CLASSES
        and count_normalized_non_whitespace_chars(block.text) >= BLOCK_MIN_CHARS
        and replacement_char_ratio(block.text) < MAX_REPLACEMENT_RATIO
    )


def evaluate_readable_content_gate(normalized_text: str, blocks: list[Block]) -> list[GateCode]:
    """Frozen AF-5 readable-content gate. Returns the gate codes that fired
    (empty list means the gate passed)."""
    codes: list[GateCode] = []
    if count_normalized_non_whitespace_chars(normalized_text) < TEXT_MIN_CHARS:
        codes.append(GateCode.TEXT_BELOW_500)
    coherent_count = sum(1 for b in blocks if is_coherent_block(b))
    if coherent_count < MIN_COHERENT_BLOCKS:
        codes.append(GateCode.COHERENT_BLOCKS_BELOW_2)
    return codes


# Frozen non-AI heading/structure rules (AF-5): a short standalone line
# matching one of these keyword sets marks the start of a new content-class
# section; every subsequent unit belongs to that class until the next
# heading. Deterministic keyword match only — never AI-classified.
HEADING_KEYWORDS: dict[str, tuple[str, ...]] = {
    "employment": ("employment", "experience", "work history", "professional experience"),
    "education": ("education", "academic background"),
    "skills": ("skills", "technical skills", "core competencies"),
    "projects": ("projects", "project experience"),
    "certifications": ("certifications", "certificates", "licenses"),
    "achievements": ("achievements", "awards", "accomplishments"),
}
MAX_HEADING_CHARS = 60


def normalize_heading_text(text: str) -> str | None:
    """Shared heading-normalization rule (length bound + case/punctuation
    strip): `None` if `text` is too long to be a heading at all, else the
    lowered/stripped comparison form. Reused by `identity_extraction.py` and
    `analysis_view.py` so all three heading-detection call sites can't
    silently drift apart into three slightly different rules."""
    if len(text) > MAX_HEADING_CHARS:
        return None
    return text.lower().strip(":•- \t")


def _match_heading(text: str) -> str | None:
    lowered = normalize_heading_text(text)
    if lowered is None:
        return None
    for content_class, keywords in HEADING_KEYWORDS.items():
        if lowered in keywords:
            return content_class
    return None


def classify_blocks(units: list[ResumeSourceUnit]) -> list[Block]:
    """Deterministic non-AI heading/structure classification (AF-5): groups
    consecutive parsed source units under the most recently seen section
    heading into one Block per section. Units preceding any heading default
    to "employment" (the most common lead section on a Resume)."""
    blocks: list[Block] = []
    current_class = "employment"
    current_texts: list[str] = []

    def flush() -> None:
        if current_texts:
            blocks.append(Block(text="\n".join(current_texts), content_class=current_class))

    for unit in units:
        heading_class = _match_heading(unit.text.strip())
        if heading_class is not None:
            flush()
            current_texts = []
            current_class = heading_class
            continue
        current_texts.append(unit.text)
    flush()
    return blocks


def remove_repeated_header_footer(pages: list[list[str]]) -> list[list[str]]:
    """Deterministically strip a line that repeats at the same position
    (first or last line) across >= 3 pages — a repeated header/footer, per
    AF-5. `pages` is a list of pages, each a list of lines in order."""
    if len(pages) < 3:
        return pages
    first_line_counts = Counter(p[0] for p in pages if p)
    last_line_counts = Counter(p[-1] for p in pages if p)
    repeated_first = {line for line, n in first_line_counts.items() if n >= 3}
    repeated_last = {line for line, n in last_line_counts.items() if n >= 3}
    cleaned: list[list[str]] = []
    for p in pages:
        lines = list(p)
        if lines and lines[0] in repeated_first:
            lines = lines[1:]
        if lines and lines[-1] in repeated_last:
            lines = lines[:-1]
        cleaned.append(lines)
    return cleaned
