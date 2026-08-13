"""Story 5.1: the gateway publication coordinator (`scan_and_publish`) —
nonterminal-membership pending state, atomic rank/tie/ordinal/cohort-count
commit on exact terminal membership, idempotency against duplicate scans/
restarts, rearm on a later terminalization, and reauthorization. Mirrors
test_candidate_finalizer.py's live-DATABASE_URL skip pattern and fixture
conventions.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.adapters.models import AnalysisRevision, AnalysisSession, CandidateResult, RevisionMembership
from src.adapters.publication_coordinator import scan_and_publish

VALID_JOB_DESCRIPTION = "x" * 200

_engine = create_engine(os.environ["DATABASE_URL"])
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


@pytest.fixture()
def db():
    session = _SessionFactory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db):
    # analysis_revisions/revision_memberships/candidate_results are shared,
    # heavily-used tables across other test files' fixtures (same lesson
    # test_candidate_finalizer.py's own truncate fixture already recorded) —
    # truncate before every test so this module's unscoped scan only ever
    # sees its own rows.
    db.execute(
        text(
            "TRUNCATE TABLE candidate_results, revision_memberships, analysis_revisions, "
            "analysis_sessions CASCADE"
        )
    )
    db.commit()
    yield


def _seed_session(db, *, status: str = "frozen_inputs") -> str:
    session_id = str(uuid.uuid4())
    db.add(
        AnalysisSession(
            id=session_id,
            creator_issuer="local",
            creator_subject=f"subject-{session_id}",
            status=status,
            created_at=datetime.now(timezone.utc),
            job_description_text=VALID_JOB_DESCRIPTION,
            job_description_version=1,
        )
    )
    db.commit()
    return session_id


def _seed_revision(db, session_id: str, *, requested_version: int, published_version: int = 0) -> str:
    revision_id = str(uuid.uuid4())
    db.add(
        AnalysisRevision(
            id=revision_id,
            analysis_session_id=session_id,
            revision_number=1,
            status="frozen",
            created_at=datetime.now(timezone.utc),
            requested_version=requested_version,
            published_version=published_version,
        )
    )
    db.commit()
    return revision_id


def _seed_membership(db, revision_id: str, *, outcome: str) -> str:
    candidate_id = str(uuid.uuid4())
    db.add(
        RevisionMembership(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            outcome=outcome,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return candidate_id


def _seed_result(db, revision_id: str, candidate_id: str, *, overall_bps: int, job_id: str | None = None):
    db.add(
        CandidateResult(
            id=str(uuid.uuid4()),
            analysis_revision_id=revision_id,
            candidate_id=candidate_id,
            candidate_job_id=job_id or str(uuid.uuid4()),
            outcome="NewResult",
            overall_score_bps_numerator=overall_bps,
            overall_score_bps_denominator=1,
            mandatory_skills_score_numerator=None,
            mandatory_skills_score_denominator=None,
            relevant_experience_score_numerator=None,
            relevant_experience_score_denominator=None,
            coverage_bps_numerator=10000,
            coverage_bps_denominator=1,
            precise_score_percent=None,
            headline_whole_percent=None,
            component_contribution_display=None,
            gate_codes=None,
            failure_category=None,
            failure_correlation_reference=None,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _revision_row(db, revision_id: str):
    return db.execute(
        text(
            "SELECT published_version, published_at, ranked_count, needs_review_count, failed_count "
            "FROM analysis_revisions WHERE id = :id"
        ),
        {"id": revision_id},
    ).mappings().one()


def _membership_row(db, revision_id: str, candidate_id: str):
    return db.execute(
        text(
            "SELECT rank_position, tie_group, presentation_ordinal FROM revision_memberships "
            "WHERE analysis_revision_id = :rid AND candidate_id = :cid"
        ),
        {"rid": revision_id, "cid": candidate_id},
    ).mappings().one()


def test_nonterminal_membership_leaves_no_rank_exposed_and_stays_pending(db):
    session_id = _seed_session(db)
    revision_id = _seed_revision(db, session_id, requested_version=1)
    _seed_membership(db, revision_id, outcome="queued")

    result = scan_and_publish(db)

    assert result is True
    row = _revision_row(db, revision_id)
    assert row["published_at"] is None
    assert row["published_version"] == 0
    assert row["ranked_count"] is None


def test_exact_terminal_membership_publishes_ranks_ties_ordinals_and_counts(db):
    session_id = _seed_session(db)
    revision_id = _seed_revision(db, session_id, requested_version=1)
    a = _seed_membership(db, revision_id, outcome="NewResult")
    b = _seed_membership(db, revision_id, outcome="NewResult")
    c = _seed_membership(db, revision_id, outcome="NeedsReview")
    _seed_result(db, revision_id, a, overall_bps=9000)
    _seed_result(db, revision_id, b, overall_bps=8000)

    result = scan_and_publish(db)

    assert result is True
    row = _revision_row(db, revision_id)
    assert row["published_at"] is not None
    assert row["published_version"] == 1
    assert row["ranked_count"] == 2
    assert row["needs_review_count"] == 1
    assert row["failed_count"] == 0

    higher = _membership_row(db, revision_id, a)
    lower = _membership_row(db, revision_id, b)
    assert higher["rank_position"] == 1
    assert lower["rank_position"] == 2
    assert higher["tie_group"] == 1
    assert lower["tie_group"] == 2
    assert higher["presentation_ordinal"] == 1
    assert lower["presentation_ordinal"] == 2

    needs_review = _membership_row(db, revision_id, c)
    assert needs_review["rank_position"] is None
    assert needs_review["tie_group"] is None
    assert needs_review["presentation_ordinal"] is None


def test_true_tie_shares_rank_and_tie_group_but_gets_distinct_presentation_ordinals(db):
    session_id = _seed_session(db)
    revision_id = _seed_revision(db, session_id, requested_version=1)
    a = _seed_membership(db, revision_id, outcome="NewResult")
    b = _seed_membership(db, revision_id, outcome="NewResult")
    _seed_result(db, revision_id, a, overall_bps=8000)
    _seed_result(db, revision_id, b, overall_bps=8000)
    # rank_order's documented tie-break (equal overall/mandatory/experience,
    # all None here) falls through to ascending candidate_key — assert
    # against that exact rule, not a guessed order.
    first_key, second_key = sorted([a, b])

    scan_and_publish(db)

    row_a = _membership_row(db, revision_id, a)
    row_b = _membership_row(db, revision_id, b)
    assert row_a["rank_position"] == row_b["rank_position"] == 1
    assert row_a["tie_group"] == row_b["tie_group"] == 1
    assert {row_a["presentation_ordinal"], row_b["presentation_ordinal"]} == {1, 2}
    first_row = _membership_row(db, revision_id, first_key)
    second_row = _membership_row(db, revision_id, second_key)
    assert first_row["presentation_ordinal"] == 1
    assert second_row["presentation_ordinal"] == 2


def test_duplicate_scan_or_restart_against_an_already_published_revision_is_idempotent(db):
    session_id = _seed_session(db)
    revision_id = _seed_revision(db, session_id, requested_version=1)
    a = _seed_membership(db, revision_id, outcome="NewResult")
    _seed_result(db, revision_id, a, overall_bps=8000)
    assert scan_and_publish(db) is True
    published_at_first = _revision_row(db, revision_id)["published_at"]

    result = scan_and_publish(db)

    assert result is False
    row = _revision_row(db, revision_id)
    assert row["published_at"] == published_at_first
    assert row["published_version"] == 1


def test_later_terminalization_rearms_publication(db):
    session_id = _seed_session(db)
    revision_id = _seed_revision(db, session_id, requested_version=1)
    a = _seed_membership(db, revision_id, outcome="NewResult")
    _seed_result(db, revision_id, a, overall_bps=8000)
    assert scan_and_publish(db) is True

    # Simulate a later terminalization bumping requested_version again
    # (Story 5.3's future retry landing a new terminal outcome, out of this
    # story's scope to construct fully — just bump the counter directly).
    db.execute(
        text("UPDATE analysis_revisions SET requested_version = requested_version + 1 WHERE id = :id"),
        {"id": revision_id},
    )
    db.commit()

    result = scan_and_publish(db)

    assert result is True
    row = _revision_row(db, revision_id)
    assert row["published_version"] == 2


def test_session_not_live_is_unreachable_but_defended(db):
    session_id = _seed_session(db, status="draft")
    revision_id = _seed_revision(db, session_id, requested_version=1)
    a = _seed_membership(db, revision_id, outcome="NewResult")
    _seed_result(db, revision_id, a, overall_bps=8000)

    result = scan_and_publish(db)

    assert result is True
    row = _revision_row(db, revision_id)
    assert row["published_at"] is None
    assert row["published_version"] == 0
    assert row["ranked_count"] is None
    membership_row = _membership_row(db, revision_id, a)
    assert membership_row["rank_position"] is None
    assert membership_row["tie_group"] is None
    assert membership_row["presentation_ordinal"] is None


def test_ranked_membership_with_no_matching_result_row_is_defended_not_crashed(db):
    """Unreachable under candidate_finalizer.py's own atomic
    CandidateResult+membership-CAS invariant, but defended anyway — a
    missing row here must not raise an uncaught KeyError (review finding:
    an unguarded lookup would abort mid-transaction and, since phase 1
    always reselects the oldest pending revision, permanently starve every
    other revision's publication behind this one)."""
    session_id = _seed_session(db)
    revision_id = _seed_revision(db, session_id, requested_version=1)
    a = _seed_membership(db, revision_id, outcome="NewResult")
    # Deliberately no _seed_result(db, revision_id, a, ...) call.

    result = scan_and_publish(db)

    assert result is True
    row = _revision_row(db, revision_id)
    assert row["published_at"] is None
    assert row["published_version"] == 0


def test_no_pending_revision_returns_false(db):
    session_id = _seed_session(db)
    _seed_revision(db, session_id, requested_version=0, published_version=0)

    assert scan_and_publish(db) is False
