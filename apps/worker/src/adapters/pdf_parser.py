"""PDF parser adapter (AF-2..AF-5 fixture-gated, AR-25). Uses the pinned
`pypdf` dependency to extract normalized text and page-based locators for
ResumeSourceUnits. No OCR (AD-19) — scanned/no-text PDFs raise ParseFatalError
so callers route them to Needs Review, never fabricated text.
"""

from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..domain.analysis_provider import PdfLocator, ResumeSourceUnit, Span
from ..domain.parse_gates import ParseFatalError, remove_repeated_header_footer

# Zero-text guard only — distinct from parse_gates.TEXT_MIN_CHARS (the real
# 500-char scoreability threshold), which is evaluated later against the
# combined ResumeSourceUnit text this function returns.
NO_TEXT_AT_ALL = 0


def parse_pdf(data: bytes) -> list[ResumeSourceUnit]:
    """Extract one ResumeSourceUnit per extracted text line, each carrying a
    PDF Locator (page + span + excerpt). Line granularity (not one unit per
    page) matches the DOCX parser's per-paragraph granularity so downstream
    heading/structure classification (AF-5) can see individual heading lines
    rather than a single page-sized blob. Raises ParseFatalError for
    password-protected, corrupt, or scanned/no-text PDFs — never fabricates
    text."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as exc:
        raise ParseFatalError(f"corrupt PDF container: {exc}") from exc

    if reader.is_encrypted:
        try:
            result = reader.decrypt("")
        except Exception as exc:  # noqa: BLE001 - any decrypt failure is fatal
            raise ParseFatalError(f"password-protected PDF: {exc}") from exc
        if result == 0:
            raise ParseFatalError("password-protected PDF: empty-password decrypt failed")

    try:
        raw_pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - any pypdf extraction failure is fatal
        raise ParseFatalError(f"pypdf extraction failed: {exc}") from exc

    line_pages = [[line for line in page.splitlines() if line.strip()] for page in raw_pages]
    cleaned_pages = remove_repeated_header_footer(line_pages)

    units: list[ResumeSourceUnit] = []
    for page_number, lines in enumerate(cleaned_pages, start=1):
        for line_number, line in enumerate(lines, start=1):
            # Span is relative to this unit's own text (matches docx_parser's
            # convention exactly) — no page-wide concatenation format is
            # assumed or required, so there is nothing for a caller's join
            # separator choice to get out of sync with.
            span = Span(start=0, end=len(line))
            locator = PdfLocator(page=page_number, span=span, excerpt=line[:200])
            units.append(ResumeSourceUnit(id=f"pdf-p{page_number}-l{line_number}", text=line, locator=locator))

    total_chars = sum(len(u.text.strip()) for u in units)
    if total_chars <= NO_TEXT_AT_ALL:
        raise ParseFatalError("no extractable text (scanned/image-only PDF)")

    return units
