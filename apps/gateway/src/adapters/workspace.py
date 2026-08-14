"""Workspace entry projection (AD-3/AR-8): the latest creator-owned
Analysis Session, and neutral direct-object access to one by id.

Reuses require_admitted_identity (AR-6, Story 1.2/2.2) and authorize_owned_row
(AD-3, Story 1.5c) as-is — this module is their first real (non-fixture-only)
caller, not a reason to write new admission or ownership primitives.
"""

from __future__ import annotations

import sys
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..domain.evidence_summary import summarize_evidence
from ..domain.identity import Identity
from ..domain.notice import RESPONSIBLE_HIRING_NOTICE
from ..domain.progress_projection import FAILED, NEEDS_REVIEW, SUCCEEDED, derive_row_state
from ..domain.scoring_configuration import Component
from .authorization import authorize_owned_row
from .db import get_db
from .identity import require_admitted_identity
from .models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateIdentity,
    CandidateJob,
    CandidateProposal,
    CandidateResult,
    Document,
    JobRequirement,
    RevisionMembership,
    Shortlist,
)

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


def _not_yet_published_results() -> dict[str, Any]:
    """A legitimately owned session with no published revision yet — a
    normal in-progress state (mid-processing, or terminal-but-not-yet-
    published per Story 5.1's publication coordinator), never a 404. A
    fresh dict every call, same discipline as `_empty_progress()`. Story 5.4
    also returns this exact shape for a `revision_number` query that
    resolves to nothing (missing, cross-session, or exists but unpublished)
    — collapsing those cases avoids letting a caller enumerate which one
    applies (Story 5.3's "neutral... without disclosing ownership"
    principle, extended to this projection's optional filter)."""
    return {
        "published": False,
        "revision_number": None,
        "published_at": None,
        "is_current": False,
        "counts": {"ranked": 0, "needs_review": 0, "failed": 0},
        "ranked": [],
        "needs_review": [],
        "failed": [],
        "notice": dict(RESPONSIBLE_HIRING_NOTICE),
    }


@router.get("/sessions/{session_id}/revisions")
def session_revisions(
    session_id: str,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 5.4 (AR-8, AR-34): the revision-selector projection. Every
    request independently re-checks admission and creator ownership, same
    pattern as `session_progress`/`session_results`. A session with no
    revisions yet (Analyze not run, or preparation still deriving) is a
    legitimately owned session with nothing to list — `{"revisions": []}`,
    never a 404. Only `revision_number`/`published`/`published_at`/
    `is_current` ever leave this function (NFR-12) — no internal `status`
    column, no `requested_version`/`published_version` counters."""
    if len(session_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND
    table = AnalysisSession.__table__
    row = authorize_owned_row(
        db, identity, table, table.c.id, table.c.creator_subject, session_id
    )
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    revision_table = AnalysisRevision.__table__
    revision_rows = (
        db.execute(
            select(revision_table.c.revision_number, revision_table.c.published_at)
            .where(revision_table.c.analysis_session_id == session_id)
            .order_by(revision_table.c.revision_number.desc())
        )
        .mappings()
        .all()
    )
    published_numbers = [r["revision_number"] for r in revision_rows if r["published_at"] is not None]
    current_published_number = max(published_numbers) if published_numbers else None

    return {
        "revisions": [
            {
                "revision_number": r["revision_number"],
                "published": r["published_at"] is not None,
                "published_at": r["published_at"].isoformat() if r["published_at"] is not None else None,
                "is_current": r["revision_number"] == current_published_number,
            }
            for r in revision_rows
        ]
    }


def _candidate_display_name(display_name: str | None, document_reference: str) -> str:
    """UX-DR10: a genuinely parsed name displays when available; otherwise
    the Document Reference is the safe, always-present fallback — never a
    blank name."""
    return display_name if display_name else document_reference


@router.get("/sessions/{session_id}/results")
def session_results(
    session_id: str,
    # Bounds match Postgres int4 range (AnalysisRevision.revision_number is
    # an Integer column) — review finding: an out-of-range value would
    # otherwise reach Postgres and raise a raw DataError/500 instead of
    # this module's own neutral-404 contract.
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 5.2 (AR-8, AR-27-33, AR-46): every read independently re-checks
    admission and creator ownership, mirroring `session_progress` exactly.
    Without `revision_number` (every existing caller), reads the current
    published revision (highest `revision_number` with `published_at IS NOT
    NULL`) exactly as before — unchanged regression-safe default. Story 5.4
    adds the optional `revision_number` filter to view a specific owned
    revision (older published, for the revision selector's "older views" —
    AC#1); a `revision_number` that doesn't exist for this session, or
    exists but isn't published, both fall through to the same
    `_not_yet_published_results()` neutral shape as "nothing published yet"
    — collapsing those cases so a tampered/stale value cannot be used to
    probe whether a given revision number exists (Story 5.3's "neutral...
    without disclosing ownership" principle). Only the fields listed below
    ever leave this function (NFR-12): no raw `candidate_proposals.items_json`
    (only its `summarize_evidence` derivation), no `candidate_jobs`
    lease/generation/token fields, no storage paths, no precise-score
    numerator/denominator pairs (only the already-rounded
    `headline_whole_percent` — precise-value reconciliation is Candidate
    Report's future scope, Story 6.1)."""
    if len(session_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND
    table = AnalysisSession.__table__
    row = authorize_owned_row(
        db, identity, table, table.c.id, table.c.creator_subject, session_id
    )
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    revision_table = AnalysisRevision.__table__

    if revision_number is None:
        # The highest-numbered published revision IS "current" by
        # definition — no second query needed to prove it (review finding:
        # the prior version ran a redundant, non-snapshotted second query
        # here even in this default path).
        revision_row = (
            db.execute(
                select(revision_table)
                .where(revision_table.c.analysis_session_id == session_id)
                .where(revision_table.c.published_at.is_not(None))
                .order_by(revision_table.c.revision_number.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        is_current = revision_row is not None
    else:
        revision_row = (
            db.execute(
                select(revision_table)
                .where(revision_table.c.analysis_session_id == session_id)
                .where(revision_table.c.revision_number == revision_number)
                .where(revision_table.c.published_at.is_not(None))
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if revision_row is not None:
            current_published_number = db.execute(
                select(revision_table.c.revision_number)
                .where(revision_table.c.analysis_session_id == session_id)
                .where(revision_table.c.published_at.is_not(None))
                .order_by(revision_table.c.revision_number.desc())
                .limit(1)
            ).scalar_one_or_none()
            is_current = revision_row["revision_number"] == current_published_number
    if revision_row is None:
        return _not_yet_published_results()

    revision_id = revision_row["id"]
    candidates_table = Candidate.__table__
    memberships_table = RevisionMembership.__table__
    documents_table = Document.__table__
    identities_table = CandidateIdentity.__table__
    results_table = CandidateResult.__table__
    shortlists_table = Shortlist.__table__

    member_rows = (
        db.execute(
            select(
                candidates_table.c.id,
                candidates_table.c.document_reference,
                documents_table.c.original_filename,
                memberships_table.c.outcome,
                memberships_table.c.rank_position,
                memberships_table.c.tie_group,
                memberships_table.c.presentation_ordinal,
                identities_table.c.display_name,
                results_table.c.id.label("result_id"),
                results_table.c.candidate_job_id,
                results_table.c.headline_whole_percent,
                results_table.c.gate_codes,
                results_table.c.failure_category,
                shortlists_table.c.state.label("shortlist_state"),
            )
            .select_from(candidates_table)
            .join(documents_table, documents_table.c.id == candidates_table.c.document_id)
            .join(
                memberships_table,
                (memberships_table.c.candidate_id == candidates_table.c.id)
                & (memberships_table.c.analysis_revision_id == revision_id),
            )
            .join(
                results_table,
                (results_table.c.candidate_id == candidates_table.c.id)
                & (results_table.c.analysis_revision_id == revision_id),
                isouter=True,
            )
            .join(identities_table, identities_table.c.candidate_id == candidates_table.c.id, isouter=True)
            .join(shortlists_table, shortlists_table.c.candidate_id == candidates_table.c.id, isouter=True)
            .order_by(documents_table.c.created_at, documents_table.c.id)
        )
        .mappings()
        .all()
    )

    # Loaded once per request, shared by every Ranked row's evidence
    # summarization below — a session's Job Requirements are frozen at
    # Revision 1 (AR-9/AR-10), so one read serves the whole projection.
    requirements_table = JobRequirement.__table__
    requirement_rows = (
        db.execute(
            select(requirements_table.c.id, requirements_table.c.canonical_text, requirements_table.c.component).where(
                requirements_table.c.analysis_session_id == session_id
            )
        )
        .mappings()
        .all()
    )
    requirement_texts: dict[str, str] = {}
    requirement_components: dict[str, Component] = {}
    for r in requirement_rows:
        try:
            requirement_components[r["id"]] = Component(r["component"])
        except ValueError:
            # Defended, not expected: a Job Requirement with a component
            # value outside the frozen AD-10 vocabulary would otherwise
            # crash requirement lookup for every Candidate in the session.
            print(f"results projection: job_requirement {r['id']} has unrecognized component {r['component']!r} — excluded from Evidence selection", file=sys.stderr)
            continue
        requirement_texts[r["id"]] = r["canonical_text"]

    proposals_table = CandidateProposal.__table__

    # ponytail: one query per ranked Candidate (N+1) rather than a single
    # batched IN (...) read — simplest correct shape at V1's ≤20-Candidate
    # demo scale. Upgrade to a single batched query keyed by candidate_job_id
    # if a cohort size measurably makes this a bottleneck.
    def _proposal_items(candidate_job_id: str | None) -> list[dict]:
        if candidate_job_id is None:
            return []
        proposal_row = (
            db.execute(select(proposals_table.c.items_json).where(proposals_table.c.candidate_job_id == candidate_job_id))
            .mappings()
            .one_or_none()
        )
        return proposal_row["items_json"] if proposal_row is not None else []

    ranked: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for member_row in member_rows:
        # Every row is independently defended, the same "skip and log" way
        # session_progress already established: a single anomalous row
        # (missing candidate_results, a Job Requirement referenced by a
        # proposal item but absent from this session, an out-of-vocabulary
        # Component value) must not take down Results visibility for every
        # other Candidate in the session.
        try:
            display_name = _candidate_display_name(member_row["display_name"], member_row["document_reference"])
            shortlist_state = member_row["shortlist_state"] or "NotShortlisted"
            outcome = member_row["outcome"]

            if outcome in ("NewResult", "ReusedResult"):
                if member_row["result_id"] is None:
                    raise ValueError("ranked membership has no matching candidate_results row")
                items = _proposal_items(member_row["candidate_job_id"])
                summary = summarize_evidence(items, requirement_texts, requirement_components)
                ranked.append(
                    {
                        "candidate_id": member_row["id"],
                        "document_reference": member_row["document_reference"],
                        "original_filename": member_row["original_filename"],
                        "display_name": display_name,
                        "rank_position": member_row["rank_position"],
                        "tie_group": member_row["tie_group"],
                        "presentation_ordinal": member_row["presentation_ordinal"],
                        "headline_whole_percent": member_row["headline_whole_percent"],
                        "strengths": [
                            {"requirement_text": p.requirement_text, "state": p.state} for p in summary.strengths
                        ],
                        "gaps": [{"requirement_text": p.requirement_text, "state": p.state} for p in summary.gaps],
                        "shortlist_state": shortlist_state,
                    }
                )
            elif outcome == "NeedsReview":
                needs_review.append(
                    {
                        "candidate_id": member_row["id"],
                        "document_reference": member_row["document_reference"],
                        "original_filename": member_row["original_filename"],
                        "display_name": display_name,
                        "gate_codes": member_row["gate_codes"] or [],
                        "shortlist_state": shortlist_state,
                    }
                )
            elif outcome == "Failed":
                failed.append(
                    {
                        "candidate_id": member_row["id"],
                        "document_reference": member_row["document_reference"],
                        "original_filename": member_row["original_filename"],
                        "display_name": display_name,
                        "failure_category": member_row["failure_category"],
                        "shortlist_state": shortlist_state,
                    }
                )
            else:
                # Unreachable for a published revision — Story 5.1's
                # publication coordinator only commits after exact terminal
                # membership.
                raise ValueError(f"non-terminal outcome {outcome!r} in a published revision")
        except (KeyError, ValueError) as exc:
            print(
                f"results projection: candidate {member_row['id']} in revision {revision_id} — {exc} — skipping",
                file=sys.stderr,
            )
            continue

    # `None`-safe: `presentation_ordinal` is guaranteed non-null for every
    # ranked row Story 5.1's coordinator commits, but the column itself is
    # nullable (AR-13) — sort defensively rather than trust that invariant
    # inside a projection two stories removed from where it's enforced.
    ranked.sort(key=lambda r: (r["presentation_ordinal"] is None, r["presentation_ordinal"]))

    return {
        "published": True,
        "revision_number": revision_row["revision_number"],
        "published_at": revision_row["published_at"].isoformat(),
        "is_current": is_current,
        "counts": {
            "ranked": revision_row["ranked_count"],
            "needs_review": revision_row["needs_review_count"],
            "failed": revision_row["failed_count"],
        },
        "ranked": ranked,
        "needs_review": needs_review,
        "failed": failed,
        "notice": dict(RESPONSIBLE_HIRING_NOTICE),
    }
