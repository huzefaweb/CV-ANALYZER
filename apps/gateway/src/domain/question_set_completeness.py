"""Gateway-side Question Set completeness gate (Story 7.2; AR-34, AR-40,
NFR-22). Pure, no DB/network/framework imports — enforced by
tests/test_domain_boundary.py.

The gateway's own re-check of a worker-staged `question_set_proposals` row
before the coordinator (`question_finalizer.py`) publishes it as an
immutable `QuestionSetVersion`. AR-34's "coordinator independently...
validates current fence/schema" means not trusting the worker's own claim of
correctness — this re-verifies count/numbering (which
`apps/worker/src/domain/question_provider.py::validate_question_shape`
already checked worker-side) and additionally checks category coverage,
which that function deliberately left out of Story 7.1's scope.

`QuestionCategory`'s five values are duplicated here, not imported — the
worker and gateway share no package (the same "port, not import" boundary
`scoring_configuration.py`/`evidence_detail.py`/`question_context.py`
already established for this exact seam).

Source linkage (a non-empty `source_requirement_id` for a non-gap-focused
item) is deliberately NOT re-validated here: Story 7.1's code review already
added `validate_question_grounding` at generation time, checked against the
exact grounded-requirement set the provider was given, and
`question_set_proposals.items_json` is written once and never mutated
(AR-17's fenced-immutable-proposal guarantee). Re-running an equivalent
check against the same immutable data would always pass or would have
already failed at generation time — it cannot catch anything new.
"""

from __future__ import annotations

QUESTION_SET_SIZE = 10

_REQUIRED_CATEGORIES = frozenset(
    {
        "technical_functional",
        "experience_verification",
        "gap_focused",
        "behavioral",
        "follow_up",
    }
)


def validate_complete_set(items: list[dict]) -> None:
    """Raise ValueError unless `items` is a complete, publishable Question
    Set: exactly ten items, unique numbers covering 1..10 exactly once,
    every one of the five frozen categories present at least once, and
    every item carries non-empty text."""
    if len(items) != QUESTION_SET_SIZE:
        raise ValueError(f"question set must contain exactly {QUESTION_SET_SIZE} items, got {len(items)}")

    numbers = [item["number"] for item in items]
    if set(numbers) != set(range(1, QUESTION_SET_SIZE + 1)):
        raise ValueError(f"question set item numbers must be exactly 1..{QUESTION_SET_SIZE}, got {sorted(numbers)}")

    categories = {item["category"] for item in items}
    missing = _REQUIRED_CATEGORIES - categories
    if missing:
        raise ValueError(f"question set is missing required categories: {sorted(missing)}")

    for item in items:
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"question set item {item.get('number')!r} has empty or missing text")
