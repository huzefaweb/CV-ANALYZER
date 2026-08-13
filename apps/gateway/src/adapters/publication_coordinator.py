"""Gateway publication coordinator (Story 5.1, AD-7, AR-18, AR-19, AR-20):
a stateless, level-triggered scan that publishes a revision only once its
exact membership is terminal, atomically committing rank/tie/presentation-
ordinal fields and cohort counts.

Two-phase shape (mirrors MODELS.md's own sequence diagram for the "P"
publisher participant), unlike `preparation_finalizer.py`/
`candidate_finalizer.py`'s single-claim-then-decide shape: those two
coordinators' claim predicate already *is* their readiness signal
(`status = 'validated'` / `status IN ('completed', 'failed')`). Publication's
`requested_version > published_version` predicate only means "something
changed since last publish," not "exact terminal membership is met" — most
scans of a revision with new activity will still find it incomplete, so an
unlocked staging read happens first, and a row lock is only taken once
staging confirms the revision is actually ready to publish.

Lock order in the locked phase — analysis_sessions, then
revision_memberships, then analysis_revisions — is deliberate and must not
be reversed. `candidate_finalizer.py` (Story 4.6) locks the owning
analysis_sessions row FIRST (its reauthorization step), a revision_memberships
row SECOND, and only much later takes an implicit lock on the
analysis_revisions row via its bare `requested_version` UPDATE — session,
then membership, then revision. (An earlier version of this coordinator
locked membership rows before the session row, which reverses the
session-vs-membership half of that order and reintroduces exactly the
deadlock this design is meant to avoid — code review caught it; fixed by
moving reauthorization first.) Locking in any other relative order here
would let a candidate_finalizer transaction holding one of these locks while
waiting on another deadlock against a publication transaction holding the
second while waiting on the first — both coordinators run concurrently on
separate threads via asyncio.to_thread. Matching candidate_finalizer.py's
exact order for all three resources avoids every such cycle.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..domain.publication import assign_ranks
from ..domain.scoring import CandidateScore, rank_order
from .models import AnalysisRevision, AnalysisSession, CandidateResult, RevisionMembership

_TERMINAL_OUTCOMES = ("NewResult", "ReusedResult", "NeedsReview", "Failed")
_RANKED_OUTCOMES = ("NewResult", "ReusedResult")


def _fraction_or_none(numerator: object | None, denominator: object | None) -> Fraction | None:
    if numerator is None or denominator is None:
        return None
    return Fraction(int(numerator), int(denominator))


def _assemble_scores(db: OrmSession, revision_id: str) -> list[CandidateScore]:
    """Reads real `candidate_results` rows for ranked outcomes and
    reconstructs `CandidateScore`s (Story 4.5's kernel is unmodified — this
    is new integration code only). Written generically over
    NewResult/ReusedResult even though this story only ever produces
    NewResult rows — ReusedResult is Story 5.3's future retry-revision
    reuse, and this query needs no rework when that lands."""
    results_table = CandidateResult.__table__
    rows = (
        db.execute(
            select(results_table)
            .where(results_table.c.analysis_revision_id == revision_id)
            .where(results_table.c.outcome.in_(_RANKED_OUTCOMES))
        )
        .mappings()
        .all()
    )
    scores: list[CandidateScore] = []
    for row in rows:
        overall = _fraction_or_none(row["overall_score_bps_numerator"], row["overall_score_bps_denominator"])
        scores.append(
            CandidateScore(
                overall_score_bps=overall,
                mandatory_skills_score=_fraction_or_none(
                    row["mandatory_skills_score_numerator"], row["mandatory_skills_score_denominator"]
                ),
                relevant_experience_score=_fraction_or_none(
                    row["relevant_experience_score_numerator"], row["relevant_experience_score_denominator"]
                ),
                candidate_key=row["candidate_id"],
            )
        )
    return scores


def scan_and_publish(db: OrmSession) -> bool:
    """Publishes at most one revision per call. Returns True if a pending
    revision was observed (whether or not it was ready to publish), False if
    there was nothing pending at all."""
    revisions_table = AnalysisRevision.__table__

    # Phase 1: unlocked scan — "compute from immutable staging" (AD-7).
    pending = (
        db.execute(
            select(revisions_table)
            .where(revisions_table.c.requested_version > revisions_table.c.published_version)
            .order_by(revisions_table.c.created_at)
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if pending is None:
        return False

    revision_id = pending["id"]
    session_id = pending["analysis_session_id"]

    memberships_table = RevisionMembership.__table__
    staging_rows = (
        db.execute(select(memberships_table).where(memberships_table.c.analysis_revision_id == revision_id))
        .mappings()
        .all()
    )
    if not staging_rows or any(row["outcome"] not in _TERMINAL_OUTCOMES for row in staging_rows):
        # Nonterminal membership (or, defensively, an unreachable empty
        # membership set) — a pending revision was observed but isn't ready.
        # AC#1: "the request remains pending." Nothing was locked/mutated at
        # this point, so db.rollback() is a no-op here — called anyway for
        # consistency with every other early-return branch below, so this
        # invariant can't be silently broken by a future edit that adds a
        # locked read above this line.
        db.rollback()
        return True

    # Phase 2: locked commit. Lock order: session, then membership rows, then
    # the revision row (see module docstring — this order is load-bearing,
    # not arbitrary — it must match candidate_finalizer.py's own order to
    # avoid a cross-coordinator deadlock).

    # Reauthorize the owning session first (background-coordinator shape: no
    # request identity to check a subject against — existence + live status
    # is the whole check, mirroring candidate_finalizer.py's own precedent
    # and its own lock ordering). Unreachable in V1 (sessions are never
    # deleted/reset post-freeze) but defended anyway.
    sessions_table = AnalysisSession.__table__
    session_row = (
        db.execute(select(sessions_table).where(sessions_table.c.id == session_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if session_row is None or session_row["status"] != "frozen_inputs":
        db.rollback()
        return True

    locked_memberships = (
        db.execute(
            select(memberships_table).where(memberships_table.c.analysis_revision_id == revision_id).with_for_update()
        )
        .mappings()
        .all()
    )
    if not locked_memberships or any(row["outcome"] not in _TERMINAL_OUTCOMES for row in locked_memberships):
        # Re-verify under lock (AR-13's terminal membership is monotonic, so
        # this regressing is unreachable, but re-check rather than trust the
        # unlocked read).
        db.rollback()
        return True

    revision_row = (
        db.execute(select(revisions_table).where(revisions_table.c.id == revision_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if revision_row is None or revision_row["requested_version"] <= revision_row["published_version"]:
        # A concurrent publication attempt already advanced published_version
        # between phase 1 and here.
        db.rollback()
        return True
    observed_version = revision_row["requested_version"]

    scores = _assemble_scores(db, revision_id)
    ordered = rank_order(scores)
    ranks_by_key = {key: (rank_position, tie_group, presentation_ordinal) for key, rank_position, tie_group, presentation_ordinal in assign_ranks(ordered)}

    # A ranked membership with no matching candidate_results row is
    # unreachable under candidate_finalizer.py's own invariant (it inserts
    # exactly one CandidateResult row in the same transaction/commit that
    # CASes a membership to "NewResult"/"ReusedResult") — but defended
    # anyway, matching this codebase's "unreachable but defended" convention
    # rather than crashing with an uncaught KeyError (review finding: an
    # unguarded lookup here would abort mid-transaction and, since phase 1's
    # claim query always reselects the oldest pending revision, a genuinely
    # anomalous row would be reselected and re-fail on every subsequent
    # scan, starving every other revision's publication behind it).
    for row in locked_memberships:
        if row["outcome"] in _RANKED_OUTCOMES and row["candidate_id"] not in ranks_by_key:
            print(
                f"publication coordinator: revision {revision_id} candidate {row['candidate_id']} "
                f"is ranked but has no matching candidate_results row — skipping this scan",
                file=sys.stderr,
            )
            db.rollback()
            return True

    for row in locked_memberships:
        if row["outcome"] not in _RANKED_OUTCOMES:
            continue
        rank_position, tie_group, presentation_ordinal = ranks_by_key[row["candidate_id"]]
        db.execute(
            memberships_table.update()
            .where(memberships_table.c.id == row["id"])
            .values(rank_position=rank_position, tie_group=tie_group, presentation_ordinal=presentation_ordinal)
        )

    ranked_count = sum(1 for row in locked_memberships if row["outcome"] in _RANKED_OUTCOMES)
    needs_review_count = sum(1 for row in locked_memberships if row["outcome"] == "NeedsReview")
    failed_count = sum(1 for row in locked_memberships if row["outcome"] == "Failed")

    now = datetime.now(timezone.utc)
    db.execute(
        revisions_table.update()
        .where(revisions_table.c.id == revision_id)
        .values(
            published_version=observed_version,
            published_at=now,
            ranked_count=ranked_count,
            needs_review_count=needs_review_count,
            failed_count=failed_count,
        )
    )

    db.commit()
    return True
