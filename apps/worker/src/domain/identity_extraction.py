"""Deterministic identity/contact extraction (Story 4.2, AD-9, UX-DR10).

Never AI — regex/heuristics only, mirroring `parse_gates.py`'s existing
non-AI heading-match approach. `CandidateIdentity` rows this feeds are
display-only (AD-9): nothing here is called from any provider/scoring path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .analysis_provider import ResumeSourceUnit
from .parse_gates import HEADING_KEYWORDS, MAX_HEADING_CHARS, normalize_heading_text

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# ponytail: unanchored, first-match-only heuristic with a bounded ceiling —
# no proximity-to-"phone"/"tel" label check, so a nearby unrelated digit run
# (an address, an ID) can occasionally match. Acceptable because
# `candidate_identities` is display-only and never scored (AD-9) — raise
# PHONE_DIGITS_MIN or add label-proximity matching if false positives become
# a real demo-visible problem.
PHONE_PATTERN = re.compile(r"(?:\+?\d[\d ()\-.]{5,18}\d)")
PHONE_DIGITS_MIN = 9
PHONE_DIGITS_MAX = 15

# Common document-title first lines that are not a candidate's name — a
# heading-keyword match alone (`_is_heading`) doesn't catch these since
# they aren't Resume *section* headings.
NON_NAME_TITLES = frozenset({"curriculum vitae", "resume", "cv", "profile", "personal profile", "resume cv"})


@dataclass(frozen=True)
class ExtractedIdentity:
    display_name: str
    name_source: Literal["parsed", "fallback"]
    email: str | None
    phone: str | None


def _is_heading(text: str) -> bool:
    lowered = normalize_heading_text(text)
    if lowered is None:
        return False
    return any(lowered in keywords for keywords in HEADING_KEYWORDS.values())


def extract_name_candidate(units: list[ResumeSourceUnit]) -> str | None:
    if not units:
        return None
    first = units[0].text.strip()
    if not first or len(first) > MAX_HEADING_CHARS:
        return None
    if _is_heading(first):
        return None
    if first.lower().strip(":•- \t") in NON_NAME_TITLES:
        return None
    if EMAIL_PATTERN.search(first) or PHONE_PATTERN.search(first):
        return None
    return first


def _extract_email(joined_text: str) -> str | None:
    match = EMAIL_PATTERN.search(joined_text)
    return match.group(0) if match else None


def _extract_phone(joined_text: str) -> str | None:
    for match in PHONE_PATTERN.finditer(joined_text):
        digits = re.sub(r"\D", "", match.group(0))
        if PHONE_DIGITS_MIN <= len(digits) <= PHONE_DIGITS_MAX:
            return match.group(0).strip()
    return None


def extract_identity(
    units: list[ResumeSourceUnit], document_reference: str, original_filename: str
) -> ExtractedIdentity:
    """Deterministic best-effort identity/contact extraction. Missing name
    falls back to Document Reference + current filename (UX-DR10's exact
    fallback content) rather than inventing one."""
    joined_text = "\n".join(u.text for u in units)
    name = extract_name_candidate(units)
    email = _extract_email(joined_text)
    phone = _extract_phone(joined_text)

    if name is not None:
        return ExtractedIdentity(display_name=name, name_source="parsed", email=email, phone=phone)
    fallback_name = f"{document_reference} — {original_filename}"
    return ExtractedIdentity(display_name=fallback_name, name_source="fallback", email=email, phone=phone)
