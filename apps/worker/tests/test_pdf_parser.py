"""PDF parser adapter tests (AC#1, AC#2). Builds fixtures in-memory with
fpdf2 (dev-only) and reads them back with the pinned pypdf-based adapter.
"""

from __future__ import annotations

import pytest
from fpdf import FPDF

from src.adapters.pdf_parser import parse_pdf
from src.domain.parse_gates import ParseFatalError


def _pdf_bytes(lines: list[str], pages: int = 1) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for _ in range(pages):
        pdf.add_page()
        pdf.multi_cell(0, 10, "\n".join(lines))
    return bytes(pdf.output())


def test_text_native_single_page_extracts_normalization_equal_excerpt():
    data = _pdf_bytes(["Backend engineer for 5 years, primarily in Python and Django."])
    units = parse_pdf(data)
    assert len(units) == 1
    assert "Backend engineer for 5 years" in units[0].text
    assert units[0].locator.page == 1
    assert units[0].locator.excerpt in units[0].text or units[0].text.startswith(
        units[0].locator.excerpt[:20]
    )


def test_multi_page_produces_units_with_correct_page_locator_per_page():
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 10, "Page one content about employment history.")
    pdf.add_page()
    pdf.multi_cell(0, 10, "Page two content about education background.")
    data = bytes(pdf.output())

    units = parse_pdf(data)
    pages = {u.locator.page for u in units}
    assert pages == {1, 2}
    page_1_text = " ".join(u.text for u in units if u.locator.page == 1)
    page_2_text = " ".join(u.text for u in units if u.locator.page == 2)
    assert "employment" in page_1_text
    assert "education" in page_2_text


def test_each_unit_span_is_relative_to_its_own_text_like_docx_locator():
    # Matches docx_parser's convention exactly: span is not an offset into
    # some page-wide concatenation (whose join-separator would be an
    # unverified assumption) — it always starts at 0 and slices the unit's
    # own text exactly.
    data = _pdf_bytes(["First line of content here.", "Second line of content here."])
    units = parse_pdf(data)
    assert len(units) == 2
    for unit in units:
        assert unit.locator.span.start == 0
        assert unit.locator.span.end == len(unit.text)
        assert unit.text[unit.locator.span.start : unit.locator.span.end] == unit.text


def test_repeated_header_removed_across_3_plus_pages():
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for body in ["Body A employment", "Body B education", "Body C skills"]:
        pdf.add_page()
        pdf.multi_cell(0, 10, f"Confidential Resume\n{body}")
    data = bytes(pdf.output())

    units = parse_pdf(data)
    assert all("Confidential Resume" not in u.text for u in units)


def test_corrupt_pdf_raises_parse_fatal():
    with pytest.raises(ParseFatalError):
        parse_pdf(b"this is not a pdf file at all")


def test_password_protected_pdf_raises_parse_fatal():
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 10, "Secret resume content.")
    pdf.set_encryption(owner_password="owner123", user_password="user456")
    data = bytes(pdf.output())

    with pytest.raises(ParseFatalError):
        parse_pdf(data)


def test_scanned_no_text_pdf_raises_parse_fatal_not_fabricated_text():
    pdf = FPDF()
    pdf.add_page()  # blank page, no text layer, no OCR performed
    data = bytes(pdf.output())

    with pytest.raises(ParseFatalError):
        parse_pdf(data)
