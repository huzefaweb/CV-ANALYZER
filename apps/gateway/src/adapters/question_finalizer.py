"""Gateway Question coordinator (Story 7.2, AR-8, AR-17, AR-18, AR-34): a
stateless, level-triggered scan that claims one terminal `question_set_jobs`
row (`status IN ('completed', 'failed')`), reauthorizes, re-validates the
staged proposal's completeness (category coverage — the check
`apps/worker/src/domain/question_provider.py::validate_question_shape`
deliberately left out of Story 7.1's scope), and atomically publishes a new
immutable `QuestionSetVersion` on success or leaves the job `'failed'` on
rejection. Mirrors `candidate_finalizer.py`'s exact structure: no
lease/token of its own — a short transaction-scoped `FOR UPDATE SKIP
LOCKED` claim is the whole reauthorization mechanism.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ..domain.question_set_completeness import validate_complete_set
from .models import (
    AnalysisSession,
    Candidate,
    QuestionSetJob,
    QuestionSetProposal,
    QuestionSetVersion,
    RevisionMembership,
)

_CLAIMABLE_STATUSES = ("completed", "failed")


def _unstick_job(db: OrmSession, question_set_job_id: str, claimed_status: str, reason: str) -> None:
    """CAS `question_set_jobs.status` straight to `'unrecoverable'` — a
    status deliberately outside `_CLAIMABLE_STATUSES` (unlike
    `candidate_finalizer.py`'s equivalent, this coordinator's own `'failed'`
    status is itself claimable, so reusing it here would leave the row
    reclaimed on every subsequent scan, defeating the whole point) — so a
    permanently unresolvable claim (orphaned candidate/session, a
    missing/stale membership row) leaves the claim pool instead of being
    reclaimed forever. Same head-of-line-blocking prevention
    `candidate_finalizer.py::_unstick_job` established for its own
    `'finalized'`-with-no-result marker. `question_set_projection.py` maps
    `'unrecoverable'` to the same public `FAILED` state as a genuine
    worker/coordinator failure — this path is a data-integrity edge case
    that is practically unreachable (the owning Candidate would already be
    gone, so no Recruiter can ever authorize a report request that would
    project it) rather than a state a real Recruiter needs to distinguish.
    Callers must `db.rollback()` first to discard the aborted attempt's
    locks before calling this."""
    jobs_table = QuestionSetJob.__table__
    db.execute(
        jobs_table.update()
        .where(jobs_table.c.id == question_set_job_id)
        .where(jobs_table.c.status == claimed_status)
        .values(status="unrecoverable", failure_reason="unstuck")
    )
    db.commit()
    print(f"question set job {question_set_job_id}: unstuck without a publish attempt — {reason}", file=sys.stderr)


def scan_and_finalize_questions(db: OrmSession) -> bool:
    """Claims and finalizes at most one terminal Question Set job. Returns
    True if a row was claimed (whether or not it advanced further), False
    if there was nothing to do."""
    jobs_table = QuestionSetJob.__table__
    claimed = (
        db.execute(
            select(jobs_table)
            .where(jobs_table.c.status.in_(_CLAIMABLE_STATUSES))
            .order_by(jobs_table.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if claimed is None:
        db.rollback()
        return False

    job = dict(claimed)
    job_id = job["id"]
    candidate_id = job["candidate_id"]
    analysis_revision_id = job["analysis_revision_id"]
    claimed_status = job["status"]

    candidates_table = Candidate.__table__
    candidate_row = (
        db.execute(select(candidates_table).where(candidates_table.c.id == candidate_id)).mappings().one_or_none()
    )
    if candidate_row is None:
        db.rollback()
        _unstick_job(db, job_id, claimed_status, "orphaned candidate_id — no matching candidates row")
        return True
    candidate = dict(candidate_row)

    # Reauthorize through the creator-owned session — same "check status,
    # never subject" shape candidate_finalizer.py/preparation_finalizer.py
    # already established for a background coordinator with no request
    # identity to check a subject against.
    sessions_table = AnalysisSession.__table__
    session_row = (
        db.execute(select(sessions_table).where(sessions_table.c.id == candidate["analysis_session_id"]).with_for_update())
        .mappings()
        .one_or_none()
    )
    if session_row is None:
        db.rollback()
        _unstick_job(db, job_id, claimed_status, "orphaned analysis_session_id — no matching row")
        return True
    if session_row["status"] != "frozen_inputs":
        db.rollback()
        _unstick_job(db, job_id, claimed_status, f"analysis_session status={session_row['status']!r} not live")
        return True

    memberships_table = RevisionMembership.__table__
    membership_row = (
        db.execute(
            select(memberships_table)
            .where(memberships_table.c.analysis_revision_id == analysis_revision_id)
            .where(memberships_table.c.candidate_id == candidate_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if membership_row is None or membership_row["outcome"] not in ("NewResult", "ReusedResult"):
        # AR-34: "coordinator independently reauthorizes ownership" — a
        # Candidate's membership outcome cannot actually change post-
        # finalization in this codebase today, but the check costs nothing
        # and matches every other coordinator's discipline here.
        db.rollback()
        _unstick_job(db, job_id, claimed_status, "revision membership missing or no longer a successful outcome")
        return True

    if claimed_status == "failed":
        # The worker already exhausted its own attempt budget (Story 7.1,
        # recovery_sweep.py). Nothing to validate or publish. 'failed' is
        # already the terminal state this story's report projection and
        # isolated retry command both key off — do not re-touch it (an
        # accepted V1 simplification: this claim query still locks and
        # releases an already-terminal row on every scan, matching
        # candidate_finalizer.py's own _CLAIMABLE_STATUSES precedent of
        # including both 'completed' and 'failed' rather than diverging for
        # a scan-cost micro-optimization at V1's single-demo scale).
        db.rollback()
        return True

    # claimed_status == "completed"
    proposals_table = QuestionSetProposal.__table__
    proposal_row = (
        db.execute(select(proposals_table).where(proposals_table.c.question_set_job_id == job_id)).mappings().one_or_none()
    )
    if proposal_row is None:
        # Defended, not expected: a 'completed' job with no staged proposal
        # would mean the worker's stage_success advanced status without
        # ever inserting a row — a worker-side contract violation.
        db.rollback()
        _unstick_job(db, job_id, claimed_status, "completed job has no matching question_set_proposals row")
        return True

    try:
        validate_complete_set(proposal_row["items_json"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        # Review finding (Blind Hunter/Edge Case Hunter, convergent High): a
        # malformed items_json shape (non-dict item, missing 'number'/
        # 'category' key, non-hashable category) previously escaped as an
        # uncaught KeyError/TypeError from validate_complete_set's dict
        # indexing, which propagated past this function, got swallowed by
        # api.py's generic per-iteration except, and left the row at
        # 'completed' — permanently head-of-line-blocking every other
        # terminal job behind it on every 2-second scan (the exact failure
        # mode `_unstick_job` exists to prevent, but never reached since the
        # exception escaped before any CAS ran). AR-34's "coordinator
        # independently... validates" means a malformed shape is exactly the
        # case this must defend against, not propagate on.
        job_cas = db.execute(
            jobs_table.update()
            .where(jobs_table.c.id == job_id)
            .where(jobs_table.c.status == "completed")
            .values(status="failed", failure_reason="incomplete_proposal")
        )
        if job_cas.rowcount != 1:
            db.rollback()
            return True
        db.commit()
        print(f"question set job {job_id}: coordinator rejected proposal — {exc}", file=sys.stderr)
        return True

    now = datetime.now(timezone.utc)
    # Review fix (Blind Hunter/Acceptance Auditor, convergent Medium): a
    # real MAX(version)+1 query, not a hardcoded literal — the Dev Notes'
    # own "not hardcoded at the call site" claim was false until this fix.
    # V1 never observes anything but 1 (a job can only be retried from
    # 'failed', never from 'published'), but the arithmetic is now actually
    # correct for the forward-compat seam the model docstring describes.
    versions_table = QuestionSetVersion.__table__
    next_version = (
        db.execute(
            select(func.coalesce(func.max(versions_table.c.version), 0) + 1)
            .where(versions_table.c.candidate_id == candidate_id)
            .where(versions_table.c.analysis_revision_id == analysis_revision_id)
        ).scalar_one()
    )
    db.add(
        QuestionSetVersion(
            id=str(uuid.uuid4()),
            question_set_job_id=job_id,
            candidate_id=candidate_id,
            analysis_revision_id=analysis_revision_id,
            version=next_version,
            items_json=proposal_row["items_json"],
            created_at=now,
        )
    )
    job_cas = db.execute(
        jobs_table.update()
        .where(jobs_table.c.id == job_id)
        .where(jobs_table.c.status == "completed")
        .values(status="published")
    )
    if job_cas.rowcount != 1:
        db.rollback()
        return True
    db.commit()
    return True
