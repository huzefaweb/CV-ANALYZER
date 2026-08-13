"""Content-type dispatch and quality-provenance assembly tests (Story 4.2, AC#1)."""

from __future__ import annotations

import io

import pytest
from docx import Document
from fpdf import FPDF

from src.adapters.resume_parsing import DOCX_CONTENT_TYPE, PDF_CONTENT_TYPE, parse_resume
from src.domain.parse_gates import GateCode
from src.domain.parse_gates import ParseFatalError
from src.domain.quality_provenance import build_quality_provenance


def _pdf_bytes(lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 10, "\n".join(lines))
    return bytes(pdf.output())


def _docx_bytes(paragraphs: list[str]) -> bytes:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_pdf_content_type_dispatches_to_pdf_parser():
    data = _pdf_bytes(["Backend engineer for 5 years, primarily in Python and Django."])
    units = parse_resume(data, PDF_CONTENT_TYPE)
    assert units[0].locator.page == 1


def test_docx_content_type_dispatches_to_docx_parser():
    data = _docx_bytes(["Backend engineer for 5 years, primarily in Python and Django."])
    units = parse_resume(data, DOCX_CONTENT_TYPE)
    assert units[0].locator.path == "body/p[1]"


def test_unsupported_content_type_raises_parse_fatal():
    with pytest.raises(ParseFatalError):
        parse_resume(b"whatever", "text/plain")


def test_corrupt_pdf_raises_parse_fatal_via_dispatch():
    with pytest.raises(ParseFatalError):
        parse_resume(b"not a pdf", PDF_CONTENT_TYPE)


def test_corrupt_docx_raises_parse_fatal_via_dispatch():
    with pytest.raises(ParseFatalError):
        parse_resume(b"not a docx", DOCX_CONTENT_TYPE)


def test_quality_provenance_classifies_sections_with_no_gate_codes():
    lines = [
        "Employment",
        "Senior backend engineer building distributed systems for six years running.",
        "Led a team of four engineers shipping production Python services reliably.",
        "Owned the on-call rotation and reduced incident response time by half.",
        "Migrated a monolithic service into independently deployable components.",
        "Education",
        "Bachelor of Science in Computer Science from a large public university.",
        "Completed coursework in algorithms, databases, and distributed systems design.",
        "Graduated with honors while working part-time as a teaching assistant.",
        "Also completed an internship focused on backend infrastructure tooling.",
    ]
    data = _pdf_bytes(lines)
    units = parse_resume(data, PDF_CONTENT_TYPE)
    provenance = build_quality_provenance(units)
    classes = {b.content_class for b in provenance.blocks}
    assert "employment" in classes
    assert "education" in classes
    assert provenance.gate_codes == []
    assert provenance.coherent_block_count >= 2


def test_quality_provenance_flags_near_empty_document():
    data = _pdf_bytes(["Hi."])
    units = parse_resume(data, PDF_CONTENT_TYPE)
    provenance = build_quality_provenance(units)
    assert GateCode.TEXT_BELOW_500 in provenance.gate_codes
    assert GateCode.COHERENT_BLOCKS_BELOW_2 in provenance.gate_codes
