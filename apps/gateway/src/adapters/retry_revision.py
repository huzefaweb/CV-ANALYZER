"""Gateway retry-revision constructor (Story 5.3, FR-8, AD-12, AD-16):
`POST /workspace/sessions/{session_id}/candidates/{candidate_id}/retry`.

Given an owned, published Analysis Revision with an eligible Failed
Candidate and an unused one-per-Document allowance, atomically constructs
the next immutable Analysis Revision: every other Candidate's result is
carried forward by lineage reference (a previously-ranked Candidate becomes
`"ReusedResult"`; `NeedsReview`/`Failed` pass through unchanged), the target
Candidate gets exactly one fresh `queued` shell job, and the Document's
allowance is consumed. Never calls a provider or touches `apps/worker` — the
existing claim/lease loop picks up the new `queued` job unchanged.

Own module/router (mirrors `new_analysis.py`/`document_upload.py`'s "own
module, own router, same `/workspace` prefix" convention) — this is a POST
command, not a GET projection, so it does not extend `workspace.py`.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..domain.document_intake import MAX_IDEMPOTENCY_KEY_LENGTH
from ..domain.identity import Identity
from ..domain.retry_eligibility import check_retry_eligibility
from .authorization import authorize_owned_row
from .db import get_db
from .identity import require_admitted_identity
from .models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateJob,
    CandidateResult,
    Document,
    RevisionMembership,
)
from .workspace import _MAX_ID_LENGTH, _NOT_FOUND

router = APIRouter(prefix="/workspace", tags=["workspace"])

# A previously-ranked Candidate (NewResult, or an already-carried
# ReusedResult from an earlier retry revision) is carried forward as
# ReusedResult; NeedsReview/Failed pass through unchanged (AD-12's "terminal
# carried outcome").
_RANKED_SOURCE_OUTCOMES = ("NewResult", "ReusedResult")

# The exact CandidateResult score/gate/failure field set candidate_finalizer.py
# writes per outcome — copied verbatim for carried-forward rows so a
# ReusedResult/carried NeedsReview/Failed row is byte-for-byte equivalent to
# its source, never recomputed.
# ponytail: hand-maintained field list, must stay in lockstep with
# candidate_finalizer.py's CandidateResult writes — if a future story adds a
# scored/gate field, update both. Upgrade path if this drifts: a shared
# constant in models.py or domain/ both modules import.
_CARRIED_RESULT_FIELDS = (
    "overall_score_bps_numerator",
    "overall_score_bps_denominator",
    "mandatory_skills_score_numerator",
    "mandatory_skills_score_denominator",
    "relevant_experience_score_numerator",
    "relevant_experience_score_denominator",
    "coverage_bps_numerator",
    "coverage_bps_denominator",
    "precise_score_percent",
    "headline_whole_percent",
    "component_contribution_display",
    "gate_codes",
    "failure_category",
    "failure_correlation_reference",
)


class RetryRequest(BaseModel):
    expected_revision_number: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)


def _uuid() -> str:
    return str(uuid.uuid4())


def _latest_revision_state(db: OrmSession, session_id: str) -> tuple[int, bool]:
    """The response-envelope pair for every no-op branch: whatever the
    latest revision (by any status) actually is. There is always at least
    Revision 1 once a session is frozen — `preparation_finalizer.py` creates
    it unpublished in the same transaction that sets `frozen_inputs`."""
    revisions_table = AnalysisRevision.__table__
    row = (
        db.execute(
            select(revisions_table)
            .where(revisions_table.c.analysis_session_id == session_id)
            .order_by(revisions_table.c.revision_number.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        # Unreachable once a session is owned (a session is only ever
        # returned by authorize_owned_row after it exists) — defended
        # anyway rather than trust that invariant blindly.
        return 0, False
    return row["revision_number"], row["published_at"] is not None


@router.post("/sessions/{session_id}/candidates/{candidate_id}/retry", status_code=202)
def retry_in_new_revision(
    session_id: str,
    candidate_id: str,
    body: RetryRequest,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, object]:
    if len(session_id) > _MAX_ID_LENGTH or len(candidate_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND

    sessions_table = AnalysisSession.__table__
    session_row = authorize_owned_row(
        db, identity, sessions_table, sessions_table.c.id, sessions_table.c.creator_subject, session_id
    )
    if session_row is None or session_row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    # Independent Candidate ownership check (Implementation Contract: "own...
    # the Candidate... independently" — a session-scoped lookup, not a bare
    # join, so a cross-session candidate_id gets the same neutral 404 a
    # cross-owner session_id already gets).
    candidates_table = Candidate.__table__
    candidate_row = (
        db.execute(
            select(candidates_table)
            .where(candidates_table.c.id == candidate_id)
            .where(candidates_table.c.analysis_session_id == session_id)
        )
        .mappings()
        .one_or_none()
    )
    if candidate_row is None:
        raise _NOT_FOUND

    # Step 1: lock the session row, recheck it is still in its one live
    # working status (mirrors publication_coordinator.py/candidate_finalizer.py's
    # own "session locked first" reauthorization order).
    locked_session = (
        db.execute(select(sessions_table).where(sessions_table.c.id == session_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if (
        locked_session is None
        or locked_session["creator_subject"] != identity.subject
        or locked_session["creator_issuer"] != identity.issuer
    ):
        # Re-verify ownership under the lock too (AR-8/CLAUDE.md: "never
        # trust a parent check already ran"), not just existence — the
        # pre-lock authorize_owned_row check above is a cheap early exit,
        # not a substitute for this one.
        db.rollback()
        raise _NOT_FOUND
    session_status = locked_session["status"]

    # Step 2: lock the target Document row (via Candidate.document_id) — the
    # allowance-consumption CAS gate. Locking this row first, before the
    # Document, is not required (Document is never locked by any other
    # coordinator today), so no deadlock-ordering concern beyond "session
    # first" applies here.
    documents_table = Document.__table__
    document_row = (
        db.execute(
            select(documents_table).where(documents_table.c.id == candidate_row["document_id"]).with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if document_row is None:
        db.rollback()
        raise _NOT_FOUND

    # Step 3: current published revision (unlocked — never mutated again
    # once published_at is set, AD-7).
    revisions_table = AnalysisRevision.__table__
    published_revision = (
        db.execute(
            select(revisions_table)
            .where(revisions_table.c.analysis_session_id == session_id)
            .where(revisions_table.c.published_at.is_not(None))
            .order_by(revisions_table.c.revision_number.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if published_revision is None:
        db.rollback()
        current_number, current_published = _latest_revision_state(db, session_id)
        return {
            "retry_created": False,
            "current_revision_number": current_number,
            "current_revision_published": current_published,
        }

    # Step 4: the target Candidate's membership on that published revision
    # (unlocked, same immutability reasoning).
    memberships_table = RevisionMembership.__table__
    source_membership = (
        db.execute(
            select(memberships_table)
            .where(memberships_table.c.analysis_revision_id == published_revision["id"])
            .where(memberships_table.c.candidate_id == candidate_id)
        )
        .mappings()
        .one_or_none()
    )

    # Step 5: allowance/replay branch, race-free under the documents-row lock.
    if document_row["retried_into_revision_id"] is not None:
        db.rollback()
        if document_row["retry_idempotency_key"] == body.idempotency_key:
            retry_revision = (
                db.execute(
                    select(revisions_table).where(revisions_table.c.id == document_row["retried_into_revision_id"])
                )
                .mappings()
                .one_or_none()
            )
            if retry_revision is not None:
                return {
                    "retry_created": True,
                    "current_revision_number": retry_revision["revision_number"],
                    "current_revision_published": retry_revision["published_at"] is not None,
                }
            # Unreachable: retried_into_revision_id is only ever set to a
            # revision this same transaction just created (Step 6) — no
            # code path deletes an AnalysisRevision row. Falls through to
            # the neutral no-op below rather than trust that invariant.
        current_number, current_published = _latest_revision_state(db, session_id)
        return {
            "retry_created": False,
            "current_revision_number": current_number,
            "current_revision_published": current_published,
        }

    eligible = source_membership is not None and check_retry_eligibility(
        session_status, source_membership["outcome"], allowance_consumed=False
    )
    stale = body.expected_revision_number != published_revision["revision_number"]
    if not eligible or stale:
        db.rollback()
        current_number, current_published = _latest_revision_state(db, session_id)
        return {
            "retry_created": False,
            "current_revision_number": current_number,
            "current_revision_published": current_published,
        }

    # Step 6: construction — mirrors preparation_finalizer.py's exact
    # AnalysisRevision/RevisionMembership/CandidateJob object-construction
    # shape for a fresh revision.
    now = datetime.now(timezone.utc)
    new_revision_id = _uuid()
    db.add(
        AnalysisRevision(
            id=new_revision_id,
            analysis_session_id=session_id,
            revision_number=published_revision["revision_number"] + 1,
            status="frozen",
            created_at=now,
            requested_version=0,
            published_version=0,
            published_at=None,
            ranked_count=None,
            needs_review_count=None,
            failed_count=None,
        )
    )

    source_memberships = (
        db.execute(
            select(memberships_table).where(memberships_table.c.analysis_revision_id == published_revision["id"])
        )
        .mappings()
        .all()
    )
    results_table = CandidateResult.__table__
    source_results_by_candidate = {
        row["candidate_id"]: row
        for row in db.execute(
            select(results_table).where(results_table.c.analysis_revision_id == published_revision["id"])
        )
        .mappings()
        .all()
    }

    for member in source_memberships:
        member_candidate_id = member["candidate_id"]
        if member_candidate_id == candidate_id:
            continue
        source_outcome = member["outcome"]
        carried_outcome = "ReusedResult" if source_outcome in _RANKED_SOURCE_OUTCOMES else source_outcome
        db.add(
            RevisionMembership(
                id=_uuid(),
                analysis_revision_id=new_revision_id,
                candidate_id=member_candidate_id,
                outcome=carried_outcome,
                created_at=now,
                rank_position=None,
                tie_group=None,
                presentation_ordinal=None,
            )
        )
        db.add(
            CandidateJob(
                id=_uuid(),
                analysis_revision_id=new_revision_id,
                candidate_id=member_candidate_id,
                status="finalized",
                created_at=now,
                generation=0,
                lease_token=None,
                lease_expires_at=None,
                state_version=0,
                reclaim_count=0,
                attempt=1,
                failure_reason=None,
            )
        )
        source_result = source_results_by_candidate.get(member_candidate_id)
        if source_result is not None:
            db.add(
                CandidateResult(
                    id=_uuid(),
                    analysis_revision_id=new_revision_id,
                    candidate_id=member_candidate_id,
                    candidate_job_id=source_result["candidate_job_id"],
                    outcome=carried_outcome,
                    created_at=now,
                    **{field: source_result[field] for field in _CARRIED_RESULT_FIELDS},
                )
            )
        else:
            # Unreachable under candidate_finalizer.py's own invariant (every
            # terminal outcome gets exactly one CandidateResult row) — logged
            # rather than silently proceeding, matching this codebase's
            # "unreachable but defended" convention elsewhere (e.g.
            # publication_coordinator.py's identical class of guard). The
            # membership/job still carry forward so AC#1's "exact
            # one-per-Candidate membership" holds; session_results already
            # skip-and-logs a ranked row with no matching result (Story 5.2).
            print(
                f"retry_revision: candidate {member_candidate_id} carried as {carried_outcome!r} "
                f"on new revision {new_revision_id} with no matching source candidate_results row",
                file=sys.stderr,
            )

    # The target Candidate: exactly one fresh queued shell — no CandidateResult
    # row (the existing worker + candidate_finalizer.py create it later,
    # exactly like a fresh Revision-1 analysis).
    db.add(
        RevisionMembership(
            id=_uuid(),
            analysis_revision_id=new_revision_id,
            candidate_id=candidate_id,
            outcome="queued",
            created_at=now,
            rank_position=None,
            tie_group=None,
            presentation_ordinal=None,
        )
    )
    db.add(
        CandidateJob(
            id=_uuid(),
            analysis_revision_id=new_revision_id,
            candidate_id=candidate_id,
            status="queued",
            created_at=now,
            generation=0,
            lease_token=None,
            lease_expires_at=None,
            state_version=0,
            reclaim_count=0,
            attempt=1,
            failure_reason=None,
        )
    )

    db.execute(
        documents_table.update()
        .where(documents_table.c.id == document_row["id"])
        .values(retried_into_revision_id=new_revision_id, retry_idempotency_key=body.idempotency_key)
    )

    try:
        db.commit()
    except IntegrityError:
        # Race window: another Failed Document in the same session was
        # retried concurrently and its transaction committed first. Both
        # transactions compute revision_number relative to the same
        # published_revision (Step 3 only ever reads the *published* one,
        # never a concurrently-created unpublished sibling), so a second
        # commit can collide with `uq_analysis_revisions_session_number`.
        # Roll back this attempt and fall back to the same neutral no-op
        # every other conflict path already returns — the source revision
        # is unchanged, and this Document's own allowance is still unused,
        # so a retried request will construct correctly against whatever
        # revision now exists.
        db.rollback()
        current_number, current_published = _latest_revision_state(db, session_id)
        return {
            "retry_created": False,
            "current_revision_number": current_number,
            "current_revision_published": current_published,
        }
    return {
        "retry_created": True,
        "current_revision_number": published_revision["revision_number"] + 1,
        "current_revision_published": False,
    }
