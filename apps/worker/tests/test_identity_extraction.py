"""Deterministic identity/contact extraction tests (Story 4.2, AC#2)."""

from __future__ import annotations

from src.domain.analysis_provider import ResumeSourceUnit
from src.domain.identity_extraction import extract_identity


def _unit(text: str) -> ResumeSourceUnit:
    return ResumeSourceUnit(id="u", text=text)


def test_name_extracted_from_first_non_heading_unit():
    units = [_unit("Jordan Rivera"), _unit("Backend engineer with 6 years of experience.")]
    result = extract_identity(units, document_reference="DOC-001", original_filename="resume.pdf")
    assert result.display_name == "Jordan Rivera"
    assert result.name_source == "parsed"


def test_heading_first_unit_falls_back_to_document_reference_and_filename():
    units = [_unit("Employment"), _unit("Backend engineer with 6 years of experience.")]
    result = extract_identity(units, document_reference="DOC-002", original_filename="resume.docx")
    assert result.name_source == "fallback"
    assert "DOC-002" in result.display_name
    assert "resume.docx" in result.display_name


def test_missing_units_falls_back_to_document_reference_and_filename():
    result = extract_identity([], document_reference="DOC-003", original_filename="cv.pdf")
    assert result.name_source == "fallback"
    assert "DOC-003" in result.display_name
    assert "cv.pdf" in result.display_name


def test_email_and_phone_extracted_from_contact_line():
    units = [
        _unit("Jordan Rivera"),
        _unit("Email: jordan.rivera@example.com | Phone: +1 (415) 555-0134"),
        _unit("Backend engineer with 6 years of experience."),
    ]
    result = extract_identity(units, document_reference="DOC-004", original_filename="resume.pdf")
    assert result.email == "jordan.rivera@example.com"
    assert result.phone is not None
    assert "555" in result.phone


def test_no_contact_info_yields_none_not_fabricated():
    units = [_unit("Jordan Rivera"), _unit("Backend engineer with 6 years of experience.")]
    result = extract_identity(units, document_reference="DOC-005", original_filename="resume.pdf")
    assert result.email is None
    assert result.phone is None


def test_document_title_first_line_falls_back_instead_of_being_treated_as_a_name():
    units = [_unit("Curriculum Vitae"), _unit("Backend engineer with 6 years of experience.")]
    result = extract_identity(units, document_reference="DOC-006", original_filename="resume.pdf")
    assert result.name_source == "fallback"


def test_date_range_is_not_misidentified_as_a_phone_number():
    units = [_unit("Jordan Rivera"), _unit("Employment 2015-2020 at a backend engineering role.")]
    result = extract_identity(units, document_reference="DOC-007", original_filename="resume.pdf")
    assert result.phone is None


def test_oversized_fallback_name_is_truncated_to_fit_the_display_name_column():
    long_filename = "a" * 260 + ".pdf"
    result = extract_identity([], document_reference="DOC-008", original_filename=long_filename)
    assert result.name_source == "fallback"
    assert len(result.display_name) > 255  # extraction itself does not truncate
