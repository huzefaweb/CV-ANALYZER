"""DOCX parser adapter (AF-2..AF-5 fixture-gated, AR-25). Uses the pinned
`python-docx` dependency to extract normalized text and stable
section/paragraph/table-path locators for ResumeSourceUnits.
"""

from __future__ import annotations

import io
import zipfile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn

from ..domain.analysis_provider import DocxLocator, ResumeSourceUnit, Span
from ..domain.parse_gates import ParseFatalError

# Zero-text guard only — distinct from parse_gates.TEXT_MIN_CHARS (the real
# 500-char scoreability threshold), which is evaluated later against the
# combined ResumeSourceUnit text this function returns.
NO_TEXT_AT_ALL = 0


def parse_docx(data: bytes) -> list[ResumeSourceUnit]:
    """Extract one ResumeSourceUnit per non-empty paragraph (body or table
    cell), in document order, with a DOCX Locator (stable path + span +
    excerpt). Raises ParseFatalError for corrupt/malformed or
    password-protected .docx containers — python-docx cannot open either
    (OOXML encryption is not a valid zip), so both surface the same way."""
    try:
        document = Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError) as exc:
        raise ParseFatalError(f"corrupt or password-protected DOCX container: {exc}") from exc

    units: list[ResumeSourceUnit] = []
    paragraph_index = 0
    table_index = 0

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph_index += 1
            text = "".join(node.text or "" for node in child.iter(qn("w:t"))).strip()
            if not text:
                continue
            path = f"body/p[{paragraph_index}]"
            units.append(_unit(path, text))
        elif child.tag == qn("w:tbl"):
            table_index += 1
            _walk_table(child, f"table[{table_index}]", units)

    total_chars = sum(len(u.text.strip()) for u in units)
    if total_chars <= NO_TEXT_AT_ALL:
        raise ParseFatalError("no extractable text in DOCX body")

    return units


def _walk_table(table_el, table_path: str, units: list[ResumeSourceUnit]) -> None:
    """Walk a table's rows/cells, recursing into any nested table found
    inside a cell (a legitimate multi-column resume template pattern) so its
    content is never silently dropped."""
    for row_idx, row in enumerate(table_el.findall(qn("w:tr")), start=1):
        for cell_idx, cell in enumerate(row.findall(qn("w:tc")), start=1):
            cell_path = f"{table_path}/row[{row_idx}]/cell[{cell_idx}]"
            p_idx = 0
            nested_table_idx = 0
            for cell_child in cell.iterchildren():
                if cell_child.tag == qn("w:p"):
                    p_idx += 1
                    text = "".join(node.text or "" for node in cell_child.iter(qn("w:t"))).strip()
                    if not text:
                        continue
                    units.append(_unit(f"{cell_path}/p[{p_idx}]", text))
                elif cell_child.tag == qn("w:tbl"):
                    nested_table_idx += 1
                    _walk_table(cell_child, f"{cell_path}/table[{nested_table_idx}]", units)


def _unit(path: str, text: str) -> ResumeSourceUnit:
    excerpt = text[:200]
    locator = DocxLocator(path=path, span=Span(start=0, end=len(text)), excerpt=excerpt)
    return ResumeSourceUnit(id=path, text=text, locator=locator)
