from __future__ import annotations

import pytest

from src.domain.failure_category import map_failure_reason_to_category

FROZEN_CATEGORIES = [
    "Analysis timed out",
    "Analysis service unavailable",
    "Analysis response could not be validated",
    "Automated analysis unavailable for this document",
    "Document processing interrupted",
]


@pytest.mark.parametrize("category", FROZEN_CATEGORIES)
def test_frozen_category_passes_through_unchanged(category: str) -> None:
    assert map_failure_reason_to_category(category) == category


def test_lease_exhausted_maps_to_document_processing_interrupted() -> None:
    assert map_failure_reason_to_category("lease_exhausted") == "Document processing interrupted"


def test_arbitrary_parse_exception_text_maps_to_unavailable_for_this_document() -> None:
    assert (
        map_failure_reason_to_category("unexpected: division by zero at offset 42")
        == "Automated analysis unavailable for this document"
    )


def test_unexpected_processing_error_literal_maps_to_unavailable_for_this_document() -> None:
    assert (
        map_failure_reason_to_category("unexpected processing error")
        == "Automated analysis unavailable for this document"
    )


def test_none_maps_to_unavailable_for_this_document() -> None:
    assert map_failure_reason_to_category(None) == "Automated analysis unavailable for this document"
