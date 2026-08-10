"""Corpus-builder self-verification (AC#3, AC#4): the frozen headline
corpus, boundary fixtures, and intake-rejection fixtures actually parse and
gate the way their metadata claims. Regenerating the builder must reproduce
this — these tests are the enforcement mechanism for that promise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.adapters.docx_parser import parse_docx
from src.adapters.pdf_parser import parse_pdf
from src.domain.parse_gates import ParseFatalError, classify_blocks, evaluate_readable_content_gate
from tests.fixtures.corpus_builder import (
    build_all,
    build_boundary_fixtures,
    build_headline_corpus,
    build_intake_rejection_fixtures,
)


def _parse(fixture):
    return parse_pdf(fixture.data) if fixture.format == "pdf" else parse_docx(fixture.data)


def _gate_codes_for(fixture) -> set[str] | None:
    """None means the parser raised ParseFatalError."""
    try:
        units = _parse(fixture)
    except ParseFatalError:
        return {"PARSE_FATAL"}
    blocks = classify_blocks(units)
    normalized_text = "".join(u.text for u in units)
    codes = evaluate_readable_content_gate(normalized_text, blocks)
    return {c.value for c in codes}


# --- AF-4 headline corpus -------------------------------------------------


def test_headline_corpus_has_exactly_20_documents_10_pdf_10_docx():
    fixtures = build_headline_corpus()
    assert len(fixtures) == 20
    assert sum(1 for f in fixtures if f.format == "pdf") == 10
    assert sum(1 for f in fixtures if f.format == "docx") == 10


def test_headline_corpus_has_18_scored_1_needs_review_1_failed():
    fixtures = build_headline_corpus()
    outcomes = [f.expected_outcome for f in fixtures]
    assert outcomes.count("scored") == 18
    assert outcomes.count("needs_review") == 1
    assert outcomes.count("failed") == 1


def test_headline_corpus_has_no_accidental_content_duplication():
    # Every fixture must be a distinct byte sequence — including the
    # designated-Failed candidate vs. the ordinary scored candidates — so the
    # 20-Document corpus is 20 genuinely distinguishable candidates, not
    # look-alikes reusing the same content under a different filename.
    fixtures = build_headline_corpus()
    fingerprints = [f.sha256 for f in fixtures]
    assert len(fingerprints) == len(set(fingerprints)), "duplicate fixture content found in the headline corpus"


def test_headline_corpus_has_a_same_filename_pair_with_distinct_content():
    fixtures = build_headline_corpus()
    by_filename: dict[str, list] = {}
    for f in fixtures:
        by_filename.setdefault(f.filename, []).append(f)
    duplicated = [group for group in by_filename.values() if len(group) > 1]
    assert len(duplicated) == 1
    pair = duplicated[0]
    assert len(pair) == 2
    assert pair[0].sha256 != pair[1].sha256


def test_headline_corpus_has_at_least_one_prompt_injection_fixture():
    fixtures = build_headline_corpus()
    assert any("prompt-injection" in f.notes or "injection" in f.notes for f in fixtures)


def test_headline_corpus_scored_and_failed_documents_pass_the_readable_content_gate():
    for f in build_headline_corpus():
        if f.expected_outcome in ("scored", "failed"):
            assert _gate_codes_for(f) == set(), f"{f.name} unexpectedly failed a gate"


def test_headline_corpus_needs_review_document_is_scanned_no_text():
    fixtures = [f for f in build_headline_corpus() if f.expected_outcome == "needs_review"]
    assert len(fixtures) == 1
    assert _gate_codes_for(fixtures[0]) == {"PARSE_FATAL"}


def test_scanned_fixture_still_raises_parse_fatal_across_regenerations():
    # The needs_review (scanned/no-text) fixture is excluded from the text-
    # content regeneration check below since it has no parsed text — but that
    # must not leave it with zero regression coverage: regenerating it must
    # still trip PARSE_FATAL, so a change in fpdf2's blank-page output that
    # accidentally started embedding a text layer would be caught.
    for _ in range(2):
        fixtures = [f for f in build_headline_corpus() if f.expected_outcome == "needs_review"]
        assert len(fixtures) == 1
        with pytest.raises(ParseFatalError):
            _parse(fixtures[0])


def test_headline_corpus_fixtures_regenerate_with_identical_text_content():
    # Byte-for-byte SHA256 identity is not achievable here: python-docx's
    # zip container and fpdf2's PDF both embed a generation timestamp, so
    # re-running the builder produces different bytes even for identical
    # content. What must be stable — and is frozen in the corpus manifest —
    # is the *parsed content* each fixture produces, which this asserts.
    def parsed_texts(fixtures):
        return {f.name: [u.text for u in _parse(f)] for f in fixtures if f.expected_outcome != "needs_review"}

    first = parsed_texts(build_headline_corpus())
    second = parsed_texts(build_headline_corpus())
    assert first == second


# --- AF-5 boundary fixtures ------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    build_boundary_fixtures(),
    ids=lambda f: f.name,
)
def test_boundary_fixture_produces_its_frozen_expected_gate_codes(fixture):
    assert _gate_codes_for(fixture) == set(fixture.expected_gate_codes)


# --- Intake-rejection fixtures ---------------------------------------------


def test_corrupt_and_password_protected_rejection_fixtures_raise_parse_fatal():
    fixtures = {f.name: f for f in build_intake_rejection_fixtures()}
    with pytest.raises(ParseFatalError):
        parse_pdf(fixtures["reject-corrupt-container"].data)
    with pytest.raises(ParseFatalError):
        parse_pdf(fixtures["reject-password-protected"].data)


def test_oversized_rejection_fixture_exceeds_10mb():
    fixtures = {f.name: f for f in build_intake_rejection_fixtures()}
    assert len(fixtures["reject-oversized"].data) > 10 * 1024 * 1024


def test_corrupt_intake_fixture_is_excluded_from_the_accepted_20():
    corpus_names = {f.name for f in build_headline_corpus()}
    rejection_names = {f.name for f in build_intake_rejection_fixtures()}
    assert corpus_names.isdisjoint(rejection_names)


def test_build_all_returns_headline_plus_boundary_plus_rejection_fixtures():
    all_fixtures = build_all()
    assert len(all_fixtures) == (
        len(build_headline_corpus()) + len(build_boundary_fixtures()) + len(build_intake_rejection_fixtures())
    )


# --- Frozen manifest file cross-check (AC#4, Task 7) -----------------------

_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "_bmad-output" / "implementation-artifacts" / "corpus-manifest.json"

_manifest_missing = pytest.mark.skipif(
    not _MANIFEST_PATH.exists(),
    reason="corpus-manifest.json not present (gitignored _bmad-output/ not materialized in this environment)",
)


@_manifest_missing
def test_frozen_manifest_records_exactly_the_20_10_10_18_1_1_corpus_counts():
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus_entries = [f for f in manifest["fixtures"] if f["category"] == "corpus"]
    assert len(corpus_entries) == 20
    assert sum(1 for f in corpus_entries if f["format"] == "pdf") == 10
    assert sum(1 for f in corpus_entries if f["format"] == "docx") == 10
    outcomes = [f["expected_outcome"] for f in corpus_entries]
    assert outcomes.count("scored") == 18
    assert outcomes.count("needs_review") == 1
    assert outcomes.count("failed") == 1


@_manifest_missing
def test_frozen_manifest_required_special_case_fixtures_are_present():
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus_entries = [f for f in manifest["fixtures"] if f["category"] == "corpus"]

    by_filename: dict[str, list] = {}
    for entry in corpus_entries:
        by_filename.setdefault(entry["filename"], []).append(entry)
    duplicated = [group for group in by_filename.values() if len(group) > 1]
    assert len(duplicated) == 1 and len(duplicated[0]) == 2
    assert duplicated[0][0]["sha256"] != duplicated[0][1]["sha256"]

    assert sum(1 for f in corpus_entries if f["expected_outcome"] == "needs_review") == 1
    assert sum(1 for f in corpus_entries if f["expected_outcome"] == "failed") == 1
    assert any("injection" in f["notes"] for f in corpus_entries)

    fingerprints = [f["sha256"] for f in corpus_entries]
    assert len(fingerprints) == len(set(fingerprints)), "manifest records duplicate fixture content"


@_manifest_missing
def test_frozen_manifest_excludes_the_corrupt_intake_fixture_from_the_20():
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus_names = {f["name"] for f in manifest["fixtures"] if f["category"] == "corpus"}
    rejection_names = {f["name"] for f in manifest["fixtures"] if f["category"] == "rejection"}
    assert "reject-corrupt-container" in rejection_names
    assert corpus_names.isdisjoint(rejection_names)
