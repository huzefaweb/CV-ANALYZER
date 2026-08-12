import pytest

from src.domain.requirement_canonicalization import (
    ALLOWED_COMPONENTS,
    CanonicalRequirement,
    ProposedRequirement,
    RequirementConflictError,
    assign_display_ids,
    canonicalize,
    merge_duplicates,
    parse_and_validate_proposal,
)
from src.domain.scoring_configuration import Component


def test_allowed_components_matches_scoring_configuration_component_enum():
    """Both modules independently declare the same seven-component
    vocabulary (review finding: nothing previously enforced agreement) —
    this fails loudly the moment one is edited without the other."""
    assert ALLOWED_COMPONENTS == {c.value for c in Component}


def test_canonicalize_nfkc_casefold_whitespace_punctuation():
    assert canonicalize("  Python, 5+ Years!  ") == "python, 5+ years"
    assert canonicalize("PYTHON") == canonicalize("python")
    # NFKC: fullwidth/compat variant collapses to the ASCII equivalent.
    assert canonicalize("Ｐython") == canonicalize("Python")
    assert canonicalize("...Leading/trailing punctuation...") == "leading/trailing punctuation"


def test_identical_multi_source_duplicates_merge_and_union_locators():
    proposed = [
        ProposedRequirement("mandatory_skills", "mandatory", "Python experience", {"start": 0, "end": 17}),
        ProposedRequirement("mandatory_skills", "mandatory", "  PYTHON EXPERIENCE  ", {"start": 40, "end": 57}),
    ]
    result = merge_duplicates(proposed)
    assert len(result) == 1
    assert result[0].source_locators == [{"start": 0, "end": 17}, {"start": 40, "end": 57}]
    assert result[0].first_source_order == 0


def test_conflicting_classification_raises():
    proposed = [
        ProposedRequirement("mandatory_skills", "mandatory", "SQL", {"start": 0, "end": 3}),
        ProposedRequirement("mandatory_skills", "preferred", "sql", {"start": 10, "end": 13}),
    ]
    try:
        merge_duplicates(proposed)
        assert False, "expected RequirementConflictError"
    except RequirementConflictError as exc:
        assert exc.component == "mandatory_skills"


def test_non_identical_statements_stay_distinct():
    proposed = [
        ProposedRequirement("mandatory_skills", "mandatory", "Python", {"start": 0, "end": 6}),
        ProposedRequirement("mandatory_skills", "mandatory", "Java", {"start": 10, "end": 14}),
    ]
    result = merge_duplicates(proposed)
    assert len(result) == 2


def test_same_text_different_component_stays_distinct():
    proposed = [
        ProposedRequirement("mandatory_skills", "mandatory", "Leadership", {"start": 0, "end": 10}),
        ProposedRequirement("domain_fit", "preferred", "Leadership", {"start": 20, "end": 30}),
    ]
    result = merge_duplicates(proposed)
    assert len(result) == 2


def test_stable_first_source_order_numbering():
    proposed = [
        ProposedRequirement("mandatory_skills", "mandatory", "B requirement", {"start": 20, "end": 33}),
        ProposedRequirement("mandatory_skills", "mandatory", "A requirement", {"start": 0, "end": 13}),
    ]
    result = merge_duplicates(proposed)
    ids = assign_display_ids(result)
    assert [display_id for display_id, _ in ids] == ["JR-001", "JR-002"]
    assert ids[0][1].canonical_text == "b requirement"
    assert ids[1][1].canonical_text == "a requirement"


def test_assign_display_ids_zero_padded():
    canonical = [
        CanonicalRequirement("mandatory_skills", "mandatory", "one", [], 0),
        CanonicalRequirement("mandatory_skills", "mandatory", "two", [], 1),
    ]
    ids = assign_display_ids(canonical)
    assert [display_id for display_id, _ in ids] == ["JR-001", "JR-002"]


JOB_DESCRIPTION_TEXT = "We need a Python engineer with 5+ years experience."


def test_parse_and_validate_proposal_accepts_well_formed_items():
    proposal_json = {
        "items": [
            {"component": "mandatory_skills", "classification": "mandatory", "text": "Python", "source_start": 11, "source_end": 17}
        ]
    }
    result = parse_and_validate_proposal(proposal_json, JOB_DESCRIPTION_TEXT)
    assert len(result) == 1
    assert result[0].component == "mandatory_skills"


def test_parse_and_validate_proposal_rejects_unsupported_component():
    proposal_json = {
        "items": [
            {"component": "not_real", "classification": "mandatory", "text": "Python", "source_start": 0, "source_end": 6}
        ]
    }
    with pytest.raises(ValueError):
        parse_and_validate_proposal(proposal_json, JOB_DESCRIPTION_TEXT)


def test_parse_and_validate_proposal_rejects_out_of_bounds_locator():
    proposal_json = {
        "items": [
            {"component": "mandatory_skills", "classification": "mandatory", "text": "Python", "source_start": 0, "source_end": 9999}
        ]
    }
    with pytest.raises(ValueError):
        parse_and_validate_proposal(proposal_json, JOB_DESCRIPTION_TEXT)


def test_parse_and_validate_proposal_rejects_non_dict():
    with pytest.raises(ValueError):
        parse_and_validate_proposal(["not", "a", "dict"], JOB_DESCRIPTION_TEXT)


def test_parse_and_validate_proposal_rejects_missing_text_key():
    proposal_json = {
        "items": [
            {"component": "mandatory_skills", "classification": "mandatory", "source_start": 0, "source_end": 6}
        ]
    }
    with pytest.raises(ValueError):
        parse_and_validate_proposal(proposal_json, JOB_DESCRIPTION_TEXT)


def test_parse_and_validate_proposal_rejects_punctuation_only_text():
    long_text = "..." + JOB_DESCRIPTION_TEXT
    proposal_json = {
        "items": [
            {"component": "mandatory_skills", "classification": "mandatory", "text": "...", "source_start": 0, "source_end": 3}
        ]
    }
    with pytest.raises(ValueError):
        parse_and_validate_proposal(proposal_json, long_text)


def test_parse_and_validate_proposal_rejects_oversized_item_count():
    proposal_json = {
        "items": [
            {"component": "mandatory_skills", "classification": "mandatory", "text": "x", "source_start": 0, "source_end": 1}
        ]
        * 1000
    }
    with pytest.raises(ValueError):
        parse_and_validate_proposal(proposal_json, "x" * 1000)
