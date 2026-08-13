"""Workspace entry projection (AD-3/AR-8): the latest creator-owned
Analysis Session, and neutral direct-object access to one by id.

Reuses require_admitted_identity (AR-6, Story 1.2/2.2) and authorize_owned_row
(AD-3, Story 1.5c) as-is — this module is their first real (non-fixture-only)
caller, not a reason to write new admission or ownership primitives.
"""

from __future__ import annotations

import sys
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..domain.identity import Identity
from ..domain.progress_projection import FAILED, NEEDS_REVIEW, SUCCEEDED, derive_row_state
from .authorization import authorize_owned_row
from .db import get_db
from .identity import require_admitted_identity
from .models import AnalysisRevision, AnalysisSession, Candidate, CandidateJob, Document, RevisionMembership

router = APIRouter(prefix="/workspace", tags=["workspace"])

# Same neutral shape Story 1.5c established for every missing/tampered/
# cross-owner object (tests/fixtures/authorization_matrix_app.py) — not a
# new convention.
_NOT_FOUND = HTTPException(status_code=404, detail="Not found")

# id is String(36) (models.py); anything longer can never match a row, but
# would otherwise reach Postgres and raise a truncation error there instead
# of falling through to the neutral 404 an oversized/tampered id must get.
_MAX_ID_LENGTH = 36


def _project(row: dict) -> dict[str, str]:
    """Only id/status/created_at ever leave this module (NFR-12) — there is
    no Job Description, Document, Candidate, or Organization field on this
    minimal Epic-2 row to accidentally widen the projection with."""
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


def _latest_owned_session_row(db: OrmSession, identity: Identity) -> Mapping[str, Any] | None:
    """The single latest owned Analysis Session row, or `None`. Shared by
    `latest_owned_session` below and Story 3.1's `new_analysis` module —
    factored out so both reuse one ownership query instead of re-deriving
    it."""
    table = AnalysisSession.__table__
    return (
        db.execute(
            select(table)
            .where(table.c.creator_issuer == identity.issuer)
            .where(table.c.creator_subject == identity.subject)
            .order_by(table.c.created_at.desc(), table.c.id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )


@router.get("")
def latest_owned_session(
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, dict[str, str] | None]:
    """AC#1/AC#2: the single latest owned session, or none — never a list."""
    row = _latest_owned_session_row(db, identity)
    return {"session": _project(row) if row is not None else None}


@router.get("/sessions/{session_id}")
def owned_session(
    session_id: str,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, str]:
    """AC#3: missing, malformed, and cross-owner ids all return the same
    neutral 404 — `authorize_owned_row` proves the subject half; the issuer
    half is checked here as a thin wrapper (see story Dev Notes for why the
    primitive itself isn't modified for this one composite-key caller)."""
    if len(session_id) > _MAX_ID_LENGTH:
        # Would otherwise reach Postgres and raise a truncation error instead
        # of the neutral 404 a malformed/tampered id must get (NFR-12).
        raise _NOT_FOUND
    table = AnalysisSession.__table__
    row = authorize_owned_row(
        db, identity, table, table.c.id, table.c.creator_subject, session_id
    )
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND
    return _project(row)


# AR-34's frozen terminal-state vocabulary — a row past finalization is
# always exactly one of these three (Task 1's derive_row_state raises on
# anything else finalized).
_TERMINAL_STATES = frozenset({SUCCEEDED, NEEDS_REVIEW, FAILED})

def _empty_progress() -> dict[str, Any]:
    # A fresh dict every call — never return shared module-level mutable
    # state by reference (review finding: a landmine for a future caller
    # that mutates the returned object in place).
    return {
        "revision_number": None,
        "rows": [],
        "aggregate": {"total": 0, "by_state": {}},
        "all_terminal": False,
        "ranking_suppressed": False,
        "view_results_available": False,
    }


@router.get("/sessions/{session_id}/progress")
def session_progress(
    session_id: str,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 4.7 (AR-34, NFR-7): every poll independently re-checks admission
    (`require_admitted_identity`) and creator ownership (`authorize_owned_row`)
    — there is no per-session cache to grow stale, since FastAPI resolves both
    dependencies fresh on every request. Per row, only `candidate_id`/
    `document_reference`/`state` ever leave this function (NFR-12) — no raw
    `candidate_jobs` status/lease field, no `revision_memberships` internal
    outcome string, and no score/rank/gate-code content (AR-19's "no
    provisional/partial ranking ever shown" — Story 5.1's `published_at`
    column is the only signal this projection ever reads; rank/score content
    stays Story 5.2's Results projection to build). `revision_number`/`aggregate`/`all_terminal`/
    `ranking_suppressed`/`view_results_available` are the projection's own
    derived summary fields, not raw internal state."""
    if len(session_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND
    table = AnalysisSession.__table__
    row = authorize_owned_row(
        db, identity, table, table.c.id, table.c.creator_subject, session_id
    )
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    # The revision lookup and the candidate/job/membership read below are two
    # separate statements, not one snapshot — under read-committed isolation
    # a finalizer commit landing between them could describe a moment where
    # the "latest revision" choice and its row set are marginally
    # inconsistent. Accepted for a polling read (the next 2.5s poll
    # self-corrects); not worth a transaction/lock for this access pattern.
    revision_table = AnalysisRevision.__table__
    revision_row = (
        db.execute(
            select(revision_table)
            .where(revision_table.c.analysis_session_id == session_id)
            .order_by(revision_table.c.revision_number.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if revision_row is None:
        # Analysis hasn't started yet — a legitimately owned session with
        # nothing to project, not a 404 (the session itself is valid).
        return _empty_progress()

    revision_id = revision_row["id"]
    candidates_table = Candidate.__table__
    memberships_table = RevisionMembership.__table__
    jobs_table = CandidateJob.__table__
    documents_table = Document.__table__
    # Review finding: candidates.created_at is NOT a real per-Document
    # timestamp — preparation_finalizer.py assigns one shared `now` to every
    # Candidate row frozen into a revision in the same transaction, so it
    # ties for every row and the "frozen order" would silently fall back to
    # id (a random UUID). documents.created_at is each Document's actual,
    # distinct upload timestamp — that is the real frozen order to sort by.
    member_rows = (
        db.execute(
            select(
                candidates_table.c.id,
                candidates_table.c.document_reference,
                memberships_table.c.outcome,
                jobs_table.c.status,
                jobs_table.c.reclaim_count,
                jobs_table.c.failure_reason,
            )
            .select_from(candidates_table)
            .join(
                documents_table,
                documents_table.c.id == candidates_table.c.document_id,
            )
            .join(
                memberships_table,
                (memberships_table.c.candidate_id == candidates_table.c.id)
                & (memberships_table.c.analysis_revision_id == revision_id),
            )
            # LEFT JOIN (review finding): a membership without a matching
            # candidate_jobs row for this revision is an anomaly under
            # Story 3.5/4.1's own one-job-per-membership invariant, not an
            # expected shape — silently INNER-JOIN-dropping it would hide a
            # Candidate from the Recruiter entirely. Surface it as a logged,
            # skipped row instead (below), matching every other coordinator
            # in this codebase's "unreachable but defended" precedent.
            .join(
                jobs_table,
                (jobs_table.c.candidate_id == candidates_table.c.id)
                & (jobs_table.c.analysis_revision_id == revision_id),
                isouter=True,
            )
            # Frozen order (AC#1's "processing order carries no rank
            # implication"): the order Documents were uploaded in, never the
            # order jobs happen to complete in.
            .order_by(documents_table.c.created_at, documents_table.c.id)
        )
        .mappings()
        .all()
    )

    projected_rows: list[dict[str, str]] = []
    by_state: dict[str, int] = {}
    for member_row in member_rows:
        if member_row["status"] is None:
            # No candidate_jobs row for this membership — unreachable given
            # Story 3.5/4.1's atomic membership+job creation; log and skip
            # rather than 500ing visibility into every other Candidate in
            # this session.
            print(
                f"progress projection: candidate {member_row['id']} has no candidate_jobs row "
                f"for revision {revision_id} — skipping",
                file=sys.stderr,
            )
            continue
        try:
            state = derive_row_state(
                member_row["status"],
                member_row["reclaim_count"],
                member_row["failure_reason"],
                member_row["outcome"],
            )
        except ValueError as exc:
            # Same defense: an internal-invariant violation on one row must
            # not take down the whole session's Progress visibility (review
            # finding — this previously propagated as an unhandled 500).
            print(f"progress projection: candidate {member_row['id']} — {exc}", file=sys.stderr)
            continue
        projected_rows.append({"candidate_id": member_row["id"], "document_reference": member_row["document_reference"], "state": state})
        # Zero-count states are omitted from `by_state` rather than listed
        # at 0 — a disclosed, arbitrary-but-consistent choice (Task 2 Dev
        # Notes), not a spec-given shape.
        by_state[state] = by_state.get(state, 0) + 1

    all_terminal = len(projected_rows) > 0 and all(r["state"] in _TERMINAL_STATES for r in projected_rows)
    return {
        "revision_number": revision_row["revision_number"],
        "rows": projected_rows,
        "aggregate": {"total": len(projected_rows), "by_state": by_state},
        "all_terminal": all_terminal,
        # Story 5.1: published_at IS NOT NULL is the real "has this revision
        # ever published" signal (revision_table is already a whole-row
        # select above, so no query change was needed to read it).
        "ranking_suppressed": all_terminal and revision_row["published_at"] is None,
        "view_results_available": revision_row["published_at"] is not None,
    }
