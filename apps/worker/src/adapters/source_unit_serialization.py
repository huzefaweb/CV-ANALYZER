"""JSON round-trip for `ResumeSourceUnit`/`Locator` (Story 4.4).

`candidate_claim.stage_parse_success` already accepts `source_units_json`
as a plain list of dicts, and Story 4.4 is the first caller that produces
that shape from real parser output (`to_json`, used to persist
`parse_artifacts`). `from_json` is the lossless inverse -- not yet called in
production (this attempt builds the analysis view from the in-memory
`units` it just parsed, not a round trip), but the exact shape a later
story reading a persisted `parse_artifacts.source_units_json` back out will
need.
"""

from __future__ import annotations

from ..domain.analysis_provider import DocxLocator, Locator, PdfLocator, ResumeSourceUnit, Span


def _locator_to_json(locator: Locator | None) -> dict | None:
    if locator is None:
        return None
    if isinstance(locator, PdfLocator):
        return {
            "type": "pdf",
            "page": locator.page,
            "span": {"start": locator.span.start, "end": locator.span.end},
            "excerpt": locator.excerpt,
        }
    return {
        "type": "docx",
        "path": locator.path,
        "span": {"start": locator.span.start, "end": locator.span.end},
        "excerpt": locator.excerpt,
    }


def _locator_from_json(data: dict | None) -> Locator | None:
    if data is None:
        return None
    span = Span(start=data["span"]["start"], end=data["span"]["end"])
    if data["type"] == "pdf":
        return PdfLocator(page=data["page"], span=span, excerpt=data["excerpt"])
    if data["type"] == "docx":
        return DocxLocator(path=data["path"], span=span, excerpt=data["excerpt"])
    raise ValueError(f"unknown locator type {data.get('type')!r}")


def to_json(units: list[ResumeSourceUnit]) -> list[dict]:
    return [{"id": u.id, "text": u.text, "locator": _locator_to_json(u.locator)} for u in units]


def from_json(data: list[dict]) -> list[ResumeSourceUnit]:
    return [
        ResumeSourceUnit(id=d["id"], text=d["text"], locator=_locator_from_json(d["locator"])) for d in data
    ]
