"""AF-12/AF-13 print-integrity proof (Story 1.5d).

Renders the two static print fixtures through the pinned, documented browser
(Chrome, via the Chrome DevTools Protocol — see
tests/fixtures/print_harness/cdp_print.py) and asserts, against the real
extracted PDF text:

- every AC#1/AC#2 required element is present;
- score/reconciliation/questions are absent from the Needs Review fixture
  (AC#2);
- no navigation/diagnostic/URL leakage (AC#3);
- the physical PDF page count matches each fixture's own printed folio
  total (AC#4) — the machine-checkable proxy for pagination integrity.

Visual overlap/clipping/unreadable-break inspection (AC#5) is not
machine-checkable within this harness and is recorded separately in
af13-print-evidence.md per the story's Implementation Contract.
"""

import io
import os
import re
import sys

import pytest
from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "print_harness"))
import cdp_print  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "print_harness")

FORBIDDEN_SUBSTRINGS = ["http://", "https://", "file://", "Sign in", "Dashboard", "Menu"]

# Distinctive fragments of each of the 10 interview questions — anchored to
# the actual question text rather than bare "{n}." digits, which also match
# unrelated decimal values in the score-reconciliation table (e.g. "91.67%",
# "10.00") and would keep passing even if a question were deleted.
INTERVIEW_QUESTION_FRAGMENTS = [
    "How did you plan and validate the Kubernetes upgrades",
    "Which Terraform controls helped you keep platform changes repeatable",
    "What was your specific role during the production migration",
    "How did you measure the reported reliability improvement",
    "What production Kafka responsibilities have you held",
    "Describe any direct ownership of cloud cost management",
    "Tell us about a severe incident you led",
    "How did you handle disagreement across application teams",
    "What did mentoring platform engineers change in team delivery",
    'What platform scale did "global services" represent',
]


def _skip_if_browser_missing():
    chrome_path = cdp_print.resolve_chrome_path()
    if not os.path.exists(chrome_path):
        pytest.skip(
            f"Documented browser not found at {chrome_path!r}; "
            "set PRINT_HARNESS_CHROME_PATH to run the AF-12 print harness."
        )


def _render(fixture_name: str) -> list[str]:
    _skip_if_browser_missing()
    path = os.path.join(FIXTURES_DIR, fixture_name)
    pdf_bytes = cdp_print.render_to_pdf(path)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [page.extract_text() for page in reader.pages]


def _normalize(text: str) -> str:
    # pypdf's extract_text() breaks lines at the PDF's own visual wrap
    # points, so a marker that spans a wrapped line (e.g. a long excerpt)
    # won't substring-match against literal spaces. Collapse all whitespace
    # runs to a single space on both sides of the comparison instead.
    return re.sub(r"\s+", " ", text)


def _assert_present(pages: list[str], markers: list[str]) -> None:
    full_text = _normalize("\n".join(pages))
    for marker in markers:
        assert _normalize(marker) in full_text, f"required marker missing from print output: {marker!r}"


def _assert_absent(pages: list[str], markers: list[str]) -> None:
    full_text = _normalize("\n".join(pages))
    for marker in markers:
        assert _normalize(marker) not in full_text, f"forbidden marker present in print output: {marker!r}"


def test_documented_browser_version_is_resolved_live():
    """AC#4/#6: the pinned browser's version is read fresh at run time, never hardcoded."""
    _skip_if_browser_missing()
    version = cdp_print.resolve_chrome_version(cdp_print.resolve_chrome_path())
    assert version, "documented browser version could not be resolved"
    assert version[0].isdigit(), f"unexpected version string shape: {version!r}"


def test_scored_fixture_contains_required_content_ac1():
    pages = _render("scored_candidate_print.html")
    _assert_present(
        pages,
        [
            "Jordan Lee",
            "Document P4M8",
            "Analysis Revision 2",
            "86%",
            "Precise calculation value: 85.63%",
            "Human verification required.",
            "Matched",
            "Partial",
            "Not Found",
            "Needs Validation",
            "Partial · Disputed",
            "Ten grounded Interview Questions",
            # Job Requirements/Evidence excerpt and locator content — not
            # merely visible in the fixture, actually asserted to survive
            # real print rendering.
            "Led production Kubernetes upgrades across 24 services and introduced deployment safety checks.",
            "PDF page 2 · Experience — Northstar Systems",
        ],
    )
    _assert_present(pages, INTERVIEW_QUESTION_FRAGMENTS)


def test_scored_fixture_pagination_matches_folio_ac4():
    pages = _render("scored_candidate_print.html")
    assert len(pages) == 6, f"expected 6 physical PDF pages, got {len(pages)}"
    for i, text in enumerate(pages, start=1):
        assert f"Page {i} of 6" in text, f"physical page {i} does not carry its own folio label"


def test_needs_review_fixture_contains_required_content_ac2():
    pages = _render("needs_review_candidate_print.html")
    _assert_present(
        pages,
        [
            "Riley Chen",
            "Document K3T9",
            "Analysis Revision 1",
            "Needs Review",
            "TEXT_BELOW_500",
            "COHERENT_BLOCKS_BELOW_2",
            "OCR is not available in V1.",
            "Human verification required.",
            "Replace this Resume in a new Analysis Session",
        ],
    )


def test_needs_review_fixture_omits_scored_content_ac2():
    pages = _render("needs_review_candidate_print.html")
    _assert_absent(
        pages,
        [
            "Precise calculation value",
            "Score reconciliation",
            "Ten grounded Interview Questions",
        ],
    )


def test_needs_review_fixture_pagination_matches_folio_ac4():
    pages = _render("needs_review_candidate_print.html")
    assert len(pages) == 1, f"expected 1 physical PDF page, got {len(pages)}"
    assert "Page 1 of 1" in pages[0]


@pytest.mark.parametrize("fixture_name", ["scored_candidate_print.html", "needs_review_candidate_print.html"])
def test_fixture_has_no_navigation_diagnostic_or_url_leakage_ac3(fixture_name):
    pages = _render(fixture_name)
    _assert_absent(pages, FORBIDDEN_SUBSTRINGS)
