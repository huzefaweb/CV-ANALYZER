"""Analysis-view builder and provider-request budget gate tests (Story 4.3, AC#1-#3)."""

from __future__ import annotations

from src.domain.analysis_provider import JobRequirement, ResumeSourceUnit
from src.domain.analysis_view import (
    EXCLUDED_HEADING_KEYWORDS,
    BudgetOverflow,
    build_analysis_view,
    check_budget,
    estimate_tokens,
)
from src.domain.parse_gates import HEADING_KEYWORDS


def _unit(unit_id: str, text: str) -> ResumeSourceUnit:
    return ResumeSourceUnit(id=unit_id, text=text)


def test_excludes_name_email_phone_and_personal_information_section():
    units = [
        _unit("u1", "Jordan Rivera"),
        _unit("u2", "Email: jordan.rivera@example.com | Phone: +1 (415) 555-0134"),
        _unit("u3", "Employment"),
        _unit("u4", "Backend engineer for 5 years, primarily in Python and Django."),
        _unit("u5", "Personal Information"),
        _unit("u6", "Date of birth: 12/05/1990"),
        _unit("u7", "Marital status: Married"),
        _unit("u8", "Nationality: Canadian"),
        _unit("u9", "123 Maple Street, Springfield, 62704"),
        _unit("u10", "Skills"),
        _unit("u11", "Python, PostgreSQL, Docker."),
    ]
    permitted = build_analysis_view(units)
    permitted_ids = [u.id for u in permitted]
    assert permitted_ids == ["u4", "u11"]


def test_permitted_units_retain_original_id_text_and_locator_unchanged():
    from src.domain.analysis_provider import PdfLocator, Span

    locator = PdfLocator(page=1, span=Span(start=0, end=10), excerpt="Backend en")
    units = [
        _unit("u1", "Employment"),
        ResumeSourceUnit(id="u2", text="Backend engineer for 5 years.", locator=locator),
    ]
    permitted = build_analysis_view(units)
    assert len(permitted) == 1
    assert permitted[0].id == "u2"
    assert permitted[0].text == "Backend engineer for 5 years."
    assert permitted[0].locator is locator


def test_allowed_section_content_is_not_dropped_by_over_matching():
    units = [
        _unit("u1", "Employment"),
        _unit("u2", "Backend engineer with 5 years of Python experience."),
        _unit("u3", "Skills"),
        _unit("u4", "Python, PostgreSQL, Docker, and AWS."),
    ]
    permitted = build_analysis_view(units)
    assert [u.id for u in permitted] == ["u2", "u4"]


def test_af7_paired_variant_produces_identical_job_related_permitted_units():
    baseline = [
        _unit("u1", "Jordan Rivera"),
        _unit("u2", "Employment"),
        _unit("u3", "Backend engineer for 5 years, primarily in Python and Django."),
        _unit("u4", "Skills"),
        _unit("u5", "Python, PostgreSQL, Docker."),
    ]
    variant = [
        _unit("u1", "Jordan Rivera"),
        _unit("u2", "Employment"),
        _unit("u3", "Backend engineer for 5 years, primarily in Python and Django."),
        _unit("u3b", "Date of birth: 12/05/1990"),
        _unit("u4", "Skills"),
        _unit("u5", "Python, PostgreSQL, Docker."),
    ]
    baseline_permitted = [(u.id, u.text) for u in build_analysis_view(baseline)]
    variant_permitted = [(u.id, u.text) for u in build_analysis_view(variant)]
    assert baseline_permitted == variant_permitted


def test_oversized_name_only_header_line_still_excluded_from_permitted_view():
    # A name-only first line too long for identity_extraction's own
    # MAX_HEADING_CHARS-based name heuristic to accept must still be
    # excluded here — build_analysis_view does not gate on that heuristic.
    long_name = "Alexander Bartholomew Cunningham-Worthington the Third, Esquire"
    assert len(long_name) > 60
    units = [
        _unit("u1", long_name),
        _unit("u2", "Employment"),
        _unit("u3", "Backend engineer for 5 years."),
    ]
    permitted = build_analysis_view(units)
    assert [u.id for u in permitted] == ["u3"]


def test_marital_pattern_does_not_over_match_ordinary_technical_vocabulary():
    units = [
        _unit("u1", "Employment"),
        _unit("u2", "Built a single page application with a single-threaded event loop."),
    ]
    permitted = build_analysis_view(units)
    assert [u.id for u in permitted] == ["u2"]


def test_zip_pattern_does_not_over_match_bare_five_digit_numbers():
    units = [
        _unit("u1", "Employment"),
        _unit("u2", "Scaled the platform to 50000 monthly active users."),
    ]
    permitted = build_analysis_view(units)
    assert [u.id for u in permitted] == ["u2"]


def test_excluded_and_allowed_heading_keywords_are_disjoint():
    allowed = {kw for keywords in HEADING_KEYWORDS.values() for kw in keywords}
    assert EXCLUDED_HEADING_KEYWORDS.isdisjoint(allowed)


def test_check_budget_returns_none_when_payload_fits():
    requirements = [JobRequirement(id="JR-1", text="3+ years of Python backend development")]
    units = [_unit("u1", "Backend engineer for 5 years, primarily in Python and Django.")]
    assert check_budget(requirements, units) is None


def test_check_budget_returns_overflow_when_resume_exceeds_budget():
    requirements = [JobRequirement(id="JR-1", text="3+ years of Python backend development")]
    huge_unit = _unit("u1", "word " * 5000)
    result = check_budget(requirements, [huge_unit])
    assert isinstance(result, BudgetOverflow)
    assert result.resume_tokens > result.resume_budget_tokens


def test_check_budget_never_mutates_or_reslices_permitted_units():
    requirements = [JobRequirement(id="JR-1", text="Python")]
    units = [_unit("u1", "Backend engineer."), _unit("u2", "Django and PostgreSQL.")]
    original_length = len(units)
    check_budget(requirements, units)
    assert len(units) == original_length
    assert units[0].id == "u1"
    assert units[1].id == "u2"


def test_estimate_tokens_is_deterministic_and_monotonic():
    text = "Backend engineer with Python experience."
    assert estimate_tokens(text) == estimate_tokens(text)
    assert estimate_tokens(text) <= estimate_tokens(text + " and PostgreSQL.")


def test_estimate_tokens_empty_string_is_zero():
    assert estimate_tokens("") == 0
