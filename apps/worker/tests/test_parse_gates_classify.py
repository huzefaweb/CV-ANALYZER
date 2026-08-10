"""Tests for the deterministic non-AI heading/structure classifier that
bridges parser output (ResumeSourceUnit) to the gate-code Block shape (AF-5).
"""

from __future__ import annotations

from src.domain.analysis_provider import ResumeSourceUnit
from src.domain.parse_gates import classify_blocks


def _unit(text: str) -> ResumeSourceUnit:
    return ResumeSourceUnit(id="u", text=text)


def test_units_before_any_heading_default_to_employment():
    units = [_unit("Senior Backend Engineer at Acme, 2019-2024, led a team of three.")]
    blocks = classify_blocks(units)
    assert blocks[0].content_class == "employment"


def test_heading_line_switches_class_and_is_not_itself_content():
    units = [
        _unit("Employment"),
        _unit("Senior Backend Engineer at Acme."),
        _unit("Education"),
        _unit("BSc Computer Science, State University."),
    ]
    blocks = classify_blocks(units)
    assert len(blocks) == 2
    assert blocks[0].content_class == "employment"
    assert "Acme" in blocks[0].text
    assert blocks[1].content_class == "education"
    assert "State University" in blocks[1].text
    assert all("Employment" != b.text and "Education" != b.text for b in blocks)


def test_consecutive_paragraphs_under_one_heading_merge_into_one_block():
    units = [
        _unit("Skills"),
        _unit("Python"),
        _unit("Django"),
        _unit("PostgreSQL"),
    ]
    blocks = classify_blocks(units)
    assert len(blocks) == 1
    assert blocks[0].content_class == "skills"
    assert "Python" in blocks[0].text and "PostgreSQL" in blocks[0].text


def test_unrecognized_heading_like_text_is_treated_as_content_not_heading():
    units = [_unit("Managed a cross-functional migration project end to end.")]
    blocks = classify_blocks(units)
    assert len(blocks) == 1
    assert "migration project" in blocks[0].text


def test_long_line_is_never_treated_as_a_heading():
    long_line = "Experience " + "x" * 60  # exceeds MAX_HEADING_CHARS even though it starts with a keyword
    units = [_unit(long_line)]
    blocks = classify_blocks(units)
    assert len(blocks) == 1
    assert blocks[0].content_class == "employment"
