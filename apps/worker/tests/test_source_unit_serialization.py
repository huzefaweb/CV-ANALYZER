from __future__ import annotations

from src.adapters.source_unit_serialization import from_json, to_json
from src.domain.analysis_provider import DocxLocator, PdfLocator, ResumeSourceUnit, Span


def test_round_trips_unit_with_pdf_locator():
    units = [
        ResumeSourceUnit(
            id="pdf-p1-l1",
            text="Backend engineer for 5 years.",
            locator=PdfLocator(page=1, span=Span(start=0, end=30), excerpt="Backend engineer for 5 years."),
        )
    ]
    assert from_json(to_json(units)) == units


def test_round_trips_unit_with_docx_locator():
    units = [
        ResumeSourceUnit(
            id="body/p[4]",
            text="Mentored two junior engineers.",
            locator=DocxLocator(path="body/p[4]", span=Span(start=0, end=31), excerpt="Mentored two junior engineers."),
        )
    ]
    assert from_json(to_json(units)) == units


def test_round_trips_unit_with_no_locator():
    units = [ResumeSourceUnit(id="unit-1", text="Skills: Python, PostgreSQL.")]
    assert from_json(to_json(units)) == units


def test_to_json_produces_plain_json_safe_dicts():
    units = [
        ResumeSourceUnit(
            id="pdf-p1-l1",
            text="Backend engineer.",
            locator=PdfLocator(page=1, span=Span(start=0, end=17), excerpt="Backend engineer."),
        )
    ]
    data = to_json(units)
    assert data == [
        {
            "id": "pdf-p1-l1",
            "text": "Backend engineer.",
            "locator": {
                "type": "pdf",
                "page": 1,
                "span": {"start": 0, "end": 17},
                "excerpt": "Backend engineer.",
            },
        }
    ]
