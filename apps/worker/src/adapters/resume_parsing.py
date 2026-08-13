"""Dispatch a Document's bytes to the matching parser adapter by content
type (Story 4.2, AR-21, AR-24).

`PDF_CONTENT_TYPE`/`DOCX_CONTENT_TYPE` mirror
`apps/gateway/src/domain/document_intake.py`'s constants of the same name —
duplicated rather than imported across the app boundary (worker and gateway
are separate deployables with separate dependency sets), the same "port,
not import" reasoning already used for `LEASE_SECONDS`/`MAX_ATTEMPTS`
between `preparation_claim.py` and `recovery_sweep.py`.
"""

from __future__ import annotations

from ..domain.analysis_provider import ResumeSourceUnit
from ..domain.parse_gates import ParseFatalError
from .docx_parser import parse_docx
from .pdf_parser import parse_pdf

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def parse_resume(data: bytes, content_type: str) -> list[ResumeSourceUnit]:
    """Dispatches to the matching parser. AF-2's intake gate already
    restricts accepted Document content types before a row ever reaches
    this table, so an unsupported type here should be unreachable in
    practice -- but it still fails closed (ParseFatalError), never a silent
    no-op, if it ever is reached."""
    if content_type == PDF_CONTENT_TYPE:
        return parse_pdf(data)
    if content_type == DOCX_CONTENT_TYPE:
        return parse_docx(data)
    raise ParseFatalError(f"unsupported content type for parsing: {content_type!r}")
