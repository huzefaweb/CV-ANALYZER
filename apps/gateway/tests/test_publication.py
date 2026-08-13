"""Story 5.1: the pure `assign_ranks` ranking-assignment kernel — standard
competition ranking (ties share position, no dense renumbering), separately
persisted but identically-computed tie_group, and strictly sequential
presentation_ordinal.
"""

from __future__ import annotations

from fractions import Fraction

from src.domain.publication import assign_ranks
from src.domain.scoring import CandidateScore


def _score(key: str, overall_bps: int) -> CandidateScore:
    return CandidateScore(
        overall_score_bps=Fraction(overall_bps, 1),
        mandatory_skills_score=None,
        relevant_experience_score=None,
        candidate_key=key,
    )


def test_no_ties_ranks_and_ordinals_are_sequential():
    scores = [_score("a", 9000), _score("b", 8000), _score("c", 7000)]
    result = assign_ranks(scores)
    assert result == [
        ("a", 1, 1, 1),
        ("b", 2, 2, 2),
        ("c", 3, 3, 3),
    ]


def test_all_tied_share_rank_and_tie_group_but_get_distinct_ordinals():
    scores = [_score("a", 8000), _score("b", 8000), _score("c", 8000)]
    result = assign_ranks(scores)
    assert result == [
        ("a", 1, 1, 1),
        ("b", 1, 1, 2),
        ("c", 1, 1, 3),
    ]


def test_partial_tie_uses_standard_competition_ranking_not_dense_rank():
    # Scores [10, 10, 5] -> ranks [1, 1, 3], never a dense [1, 1, 2].
    scores = [_score("a", 10), _score("b", 10), _score("c", 5)]
    result = assign_ranks(scores)
    assert result == [
        ("a", 1, 1, 1),
        ("b", 1, 1, 2),
        ("c", 3, 3, 3),
    ]


def test_single_candidate():
    result = assign_ranks([_score("a", 5000)])
    assert result == [("a", 1, 1, 1)]


def test_empty_list_returns_empty_not_an_error():
    assert assign_ranks([]) == []
