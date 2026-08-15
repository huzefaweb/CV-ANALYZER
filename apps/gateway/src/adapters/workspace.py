"""Workspace entry projection (AD-3/AR-8): the latest creator-owned
Analysis Session, and neutral direct-object access to one by id.

Reuses require_admitted_identity (AR-6, Story 1.2/2.2) and authorize_owned_row
(AD-3, Story 1.5c) as-is — this module is their first real (non-fixture-only)
caller, not a reason to write new admission or ownership primitives.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..domain.evidence_detail import build_evidence_rows
from ..domain.evidence_summary import summarize_evidence
from ..domain.identity import Identity
from ..domain.notice import RESPONSIBLE_HIRING_NOTICE
from ..domain.print_projection import SCORED_COMBINED, derive_print_scope, derive_trigger, is_print_blocked
from ..domain.progress_projection import FAILED, NEEDS_REVIEW, SUCCEEDED, derive_row_state
from ..domain.question_set_projection import derive_question_set_state
from ..domain.scoring_configuration import BASE_WEIGHT_BPS, Component
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
    EvidenceReview,
    JobRequirement,
    ParseArtifact,
    QuestionSetJob,
    QuestionSetProposal,
    QuestionSetVersion,
    RevisionMembership,
    ScoringConfiguration,
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


def _resolve_published_revision(
    db: OrmSession, session_id: str, revision_number: int | None
) -> tuple[Mapping[str, Any] | None, bool]:
    """Shared by `session_results` and `candidate_report` (Story 6.1 review
    finding: this ~35-line shape was duplicated verbatim between the two —
    factored out once a second caller needed it, matching this file's own
    established reuse discipline). `None` `revision_number` resolves to the
    highest-numbered published revision (`is_current` trivially `True` by
    definition — no second query needed to prove it, Story 5.2 review
    finding). A given `revision_number` is looked up scoped to `session_id`
    and `published_at IS NOT NULL`; a miss returns `(None, False)`,
    collapsing "doesn't exist," "exists but unpublished," and "belongs to
    another session" into one outcome so a tampered value cannot be used to
    probe revision existence (Story 5.3's neutral-response principle)."""
    revision_table = AnalysisRevision.__table__
    if revision_number is None:
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
        return revision_row, revision_row is not None

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
    if revision_row is None:
        return None, False
    current_published_number = db.execute(
        select(revision_table.c.revision_number)
        .where(revision_table.c.analysis_session_id == session_id)
        .where(revision_table.c.published_at.is_not(None))
        .order_by(revision_table.c.revision_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    return revision_row, revision_row["revision_number"] == current_published_number


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

    revision_row, is_current = _resolve_published_revision(db, session_id, revision_number)
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
                shortlists_table.c.version.label("shortlist_version"),
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
            select(
                requirements_table.c.id,
                requirements_table.c.display_id,
                requirements_table.c.canonical_text,
                requirements_table.c.component,
            ).where(requirements_table.c.analysis_session_id == session_id)
        )
        .mappings()
        .all()
    )
    requirement_texts: dict[str, str] = {}
    requirement_components: dict[str, Component] = {}
    for r in requirement_rows:
        # Keyed by display_id, not the row's own UUID `id` — see
        # candidate_finalizer.py's _assemble_scoreability_inputs for why
        # (same fix, same bug, every inline copy of this pattern).
        try:
            requirement_components[r["display_id"]] = Component(r["component"])
        except ValueError:
            # Defended, not expected: a Job Requirement with a component
            # value outside the frozen AD-10 vocabulary would otherwise
            # crash requirement lookup for every Candidate in the session.
            print(f"results projection: job_requirement {r['id']} has unrecognized component {r['component']!r} — excluded from Evidence selection", file=sys.stderr)
            continue
        requirement_texts[r["display_id"]] = r["canonical_text"]

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
            shortlist_version = member_row["shortlist_version"] or 1
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
                        "shortlist_version": shortlist_version,
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
                        "shortlist_version": shortlist_version,
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
                        "shortlist_version": shortlist_version,
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


def _authorize_candidate_revision(
    db: OrmSession, identity: Identity, candidate_id: str, revision_number: int | None
) -> tuple[Mapping[str, Any], Mapping[str, Any], bool]:
    """Two-hop ownership check (AR-8) shared by `candidate_report` and the
    Story 6.2 `candidate_evidence`/`candidate_reconciliation` endpoints —
    extracted once a third caller needed the identical shape, matching this
    file's own established reuse discipline (`_resolve_published_revision`'s
    Story 6.1 extraction). Unlike every session-scoped endpoint in this
    module, the route carries no `session_id`, so ownership is a genuine
    two-hop independent re-check (AR-8): look up the Candidate's
    `analysis_session_id`, then prove creator ownership of that session,
    never trusting that a Candidate row existing implies anything about who
    owns it. Raises `_NOT_FOUND` on any miss — missing/oversized candidate
    id, cross-owner session, or a stale/unpublished/nonexistent revision
    number all collapse into the same neutral outcome (AC#3)."""
    if len(candidate_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND

    candidates_table = Candidate.__table__
    candidate_row = (
        db.execute(select(candidates_table).where(candidates_table.c.id == candidate_id)).mappings().one_or_none()
    )
    if candidate_row is None:
        raise _NOT_FOUND

    session_table = AnalysisSession.__table__
    session_row = authorize_owned_row(
        db,
        identity,
        session_table,
        session_table.c.id,
        session_table.c.creator_subject,
        candidate_row["analysis_session_id"],
    )
    if session_row is None or session_row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    revision_row, is_current = _resolve_published_revision(
        db, candidate_row["analysis_session_id"], revision_number
    )
    if revision_row is None:
        raise _NOT_FOUND

    return candidate_row, revision_row, is_current


@router.get("/candidates/{candidate_id}/report")
def candidate_report(
    candidate_id: str,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 6.1 (AR-8, AR-12, AR-21, AR-27-33, AR-46): the authorized,
    single-Candidate Report projection. Unlike every other endpoint in this
    module, the route carries no `session_id` (Story 5.2 already shipped the
    forward-referencing link as `/candidates/{candidate_id}/report`) —
    ownership is therefore a genuine two-hop independent re-check (AR-8),
    performed by the shared `_authorize_candidate_revision` helper (Story
    6.2 extraction, also used by `candidate_evidence`/`candidate_
    reconciliation`): look up the Candidate's `analysis_session_id`, then
    prove creator ownership of that session, never trusting that a
    Candidate row existing implies anything about who owns it.
    `revision_number` mirrors `session_results`'s identical optional
    filter (Story 5.4); omitted, it
    resolves to the highest-numbered published revision for this Candidate's
    session. Failed membership, a missing/malformed/cross-owner id, or a
    stale/unpublished revision number all collapse into the same neutral
    `_NOT_FOUND` (AC#3) — never a distinguishing response. NFR-12: only the
    fields assembled below ever leave this function — no raw `items_json`,
    no `candidate_jobs` lease/token fields, no storage paths, no component
    numerator/denominator pairs (only the already-rounded
    `precise_score_percent`; the full weight/contribution breakdown is
    Story 6.2's scope)."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    candidates_table = Candidate.__table__

    revision_id = revision_row["id"]
    documents_table = Document.__table__
    memberships_table = RevisionMembership.__table__
    identities_table = CandidateIdentity.__table__
    results_table = CandidateResult.__table__
    shortlists_table = Shortlist.__table__
    question_set_jobs_table = QuestionSetJob.__table__

    member_row = (
        db.execute(
            select(
                candidates_table.c.document_reference,
                documents_table.c.original_filename,
                memberships_table.c.outcome,
                identities_table.c.display_name,
                results_table.c.id.label("result_id"),
                results_table.c.candidate_job_id,
                results_table.c.headline_whole_percent,
                results_table.c.precise_score_percent,
                results_table.c.gate_codes,
                shortlists_table.c.state.label("shortlist_state"),
                shortlists_table.c.version.label("shortlist_version"),
                question_set_jobs_table.c.id.label("question_set_job_id"),
                question_set_jobs_table.c.status.label("question_set_job_status"),
                question_set_jobs_table.c.reclaim_count.label("question_set_reclaim_count"),
                question_set_jobs_table.c.failure_reason.label("question_set_failure_reason"),
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
            .join(
                question_set_jobs_table,
                # Code review fix (Acceptance Auditor, High): must also
                # match this revision — question_set_jobs is now
                # revision-scoped (migration d2e3f4a5b6c7), and an
                # unfiltered join on candidate_id alone leaked a job
                # belonging to a *different* revision's report view.
                (question_set_jobs_table.c.candidate_id == candidates_table.c.id)
                & (question_set_jobs_table.c.analysis_revision_id == revision_id),
                isouter=True,
            )
            .where(candidates_table.c.id == candidate_id)
        )
        .mappings()
        .one_or_none()
    )
    # Failed membership has no Candidate Report (AC#3); a missing row or any
    # non-terminal/unreachable outcome collapses into the same neutral 404.
    if member_row is None or member_row["outcome"] not in ("NewResult", "ReusedResult", "NeedsReview"):
        raise _NOT_FOUND

    try:
        display_name = _candidate_display_name(member_row["display_name"], member_row["document_reference"])
        shortlist_state = member_row["shortlist_state"] or "NotShortlisted"
        shortlist_version = member_row["shortlist_version"] or 1
        outcome = member_row["outcome"]

        requirements_table = JobRequirement.__table__
        requirement_rows = (
            db.execute(
                select(
                    requirements_table.c.id,
                    requirements_table.c.display_id,
                    requirements_table.c.canonical_text,
                    requirements_table.c.component,
                ).where(requirements_table.c.analysis_session_id == candidate_row["analysis_session_id"])
            )
            .mappings()
            .all()
        )
        requirement_texts: dict[str, str] = {}
        requirement_components: dict[str, Component] = {}
        for r in requirement_rows:
            # Keyed by display_id, not the row's own UUID `id` — see
            # candidate_finalizer.py's _assemble_scoreability_inputs for why
            # (same fix, same bug, every inline copy of this pattern).
            try:
                requirement_components[r["display_id"]] = Component(r["component"])
            except ValueError:
                print(
                    f"candidate_report: job_requirement {r['id']} has unrecognized component {r['component']!r} — excluded from Evidence selection",
                    file=sys.stderr,
                )
                continue
            requirement_texts[r["display_id"]] = r["canonical_text"]

        proposals_table = CandidateProposal.__table__
        items: list[dict] = []
        if member_row["candidate_job_id"] is not None:
            proposal_row = (
                db.execute(
                    select(proposals_table.c.items_json).where(
                        proposals_table.c.candidate_job_id == member_row["candidate_job_id"]
                    )
                )
                .mappings()
                .one_or_none()
            )
            items = proposal_row["items_json"] if proposal_row is not None else []

        # Reused unchanged from Story 5.2 — same capped strengths/gaps
        # selection. `interview_focus` intentionally reuses `gaps` rather
        # than a second selection function (see story Dev Notes).
        summary = summarize_evidence(items, requirement_texts, requirement_components)
        strengths = [{"requirement_text": p.requirement_text, "state": p.state} for p in summary.strengths]
        gaps = [{"requirement_text": p.requirement_text, "state": p.state} for p in summary.gaps]

        report: dict[str, Any] = {
            "analysis_session_id": candidate_row["analysis_session_id"],
            "candidate_id": candidate_id,
            "document_reference": member_row["document_reference"],
            "original_filename": member_row["original_filename"],
            "display_name": display_name,
            "outcome": "Ranked" if outcome in ("NewResult", "ReusedResult") else "NeedsReview",
            "revision_number": revision_row["revision_number"],
            "revision_created_at": revision_row["created_at"].isoformat(),
            "published_at": revision_row["published_at"].isoformat(),
            "is_current": is_current,
            "shortlist_state": shortlist_state,
            "shortlist_version": shortlist_version,
            "strengths": strengths,
            "gaps": gaps,
            "interview_focus": gaps,
            "notice": dict(RESPONSIBLE_HIRING_NOTICE),
        }

        if outcome in ("NewResult", "ReusedResult"):
            if member_row["result_id"] is None:
                raise ValueError("ranked membership has no matching candidate_results row")
            # AC#2: score/reconciliation fields never appear at all for
            # Needs Review — omit the keys entirely, not merely null.
            report["headline_whole_percent"] = member_row["headline_whole_percent"]
            report["precise_score_percent"] = (
                str(member_row["precise_score_percent"])
                if member_row["precise_score_percent"] is not None
                else None
            )
            # Story 7.2: the full six-state vocabulary
            # (NotGenerated/Generating/Recovering/Retrying/Complete/Failed),
            # derived from question_set_jobs' internal status/reclaim_count/
            # failure_reason by the pure question_set_projection module.
            report["question_set_state"] = derive_question_set_state(
                member_row["question_set_job_status"],
                member_row["question_set_reclaim_count"] or 0,
                member_row["question_set_failure_reason"],
            )
        else:
            report["gate_codes"] = member_row["gate_codes"] or []

        return report
    except (KeyError, ValueError, TypeError) as exc:
        # A single-candidate endpoint has no "skip this row, keep the rest"
        # option the way session_results does — an anomalous row here is a
        # neutral unavailable outcome, same as any other malformed case.
        # TypeError included (review finding): a malformed, non-Mapping
        # items_json entry raises TypeError from summarize_evidence's own
        # item.get()/item[...] access, not KeyError/ValueError.
        print(f"candidate_report: candidate {candidate_id} in revision {revision_id} — {exc}", file=sys.stderr)
        raise _NOT_FOUND


def _review_projection(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """Story 6.3: row absence -> the implicit default state (never
    disputed, version 0) — shared by `candidate_evidence`'s read and
    `evidence_review`'s own response bodies so both endpoints describe
    "no review flag" identically."""
    if row is None:
        return {"state": "NoReviewFlag", "version": 0}
    return {"state": "Disputed" if row["disputed"] else "NoReviewFlag", "version": row["version"]}


@router.get("/candidates/{candidate_id}/evidence")
def candidate_evidence(
    candidate_id: str,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 6.2 (AC#1, AC#3; AR-8, AR-12, AR-21, AR-25): the authorized,
    full per-Requirement Evidence projection — every Matched/Partial/Needs
    Validation/Not Found row, with locator/excerpt, never the capped
    strengths/gaps summary `candidate_report` already renders. Same
    two-hop ownership shape as `candidate_report` (`_authorize_candidate_
    revision`); qualifies for the same outcomes (`NewResult`/`ReusedResult`/
    `NeedsReview` — Failed has no Evidence view either, AC#3). NFR-12: only
    the fields assembled below ever leave this function — no raw
    `parse_artifacts.source_units_json` (only the derived locator
    description per matched item), no full Resume text, no `candidate_jobs`
    lease fields, no storage paths."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    candidates_table = Candidate.__table__
    revision_id = revision_row["id"]

    memberships_table = RevisionMembership.__table__
    results_table = CandidateResult.__table__
    member_row = (
        db.execute(
            select(
                memberships_table.c.outcome,
                results_table.c.candidate_job_id,
            )
            .select_from(candidates_table)
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
            .where(candidates_table.c.id == candidate_id)
        )
        .mappings()
        .one_or_none()
    )
    if member_row is None or member_row["outcome"] not in ("NewResult", "ReusedResult", "NeedsReview"):
        raise _NOT_FOUND

    try:
        proposals_table = CandidateProposal.__table__
        items: list[dict] = []
        if member_row["candidate_job_id"] is not None:
            proposal_row = (
                db.execute(
                    select(proposals_table.c.items_json).where(
                        proposals_table.c.candidate_job_id == member_row["candidate_job_id"]
                    )
                )
                .mappings()
                .one_or_none()
            )
            items = proposal_row["items_json"] if proposal_row is not None else []

        requirements_table = JobRequirement.__table__
        requirement_rows = (
            db.execute(
                select(
                    requirements_table.c.id,
                    requirements_table.c.display_id,
                    requirements_table.c.canonical_text,
                    requirements_table.c.component,
                ).where(requirements_table.c.analysis_session_id == candidate_row["analysis_session_id"])
            )
            .mappings()
            .all()
        )
        requirement_texts: dict[str, str] = {}
        requirement_display_ids: dict[str, str] = {}
        requirement_components: dict[str, Component] = {}
        for r in requirement_rows:
            # Keyed by display_id, not the row's own UUID `id` — see
            # candidate_finalizer.py's _assemble_scoreability_inputs for why
            # (same fix, same bug, every inline copy of this pattern).
            try:
                requirement_components[r["display_id"]] = Component(r["component"])
            except ValueError:
                print(
                    f"candidate_evidence: job_requirement {r['id']} has unrecognized component {r['component']!r} — excluded from Evidence selection",
                    file=sys.stderr,
                )
                continue
            requirement_texts[r["display_id"]] = r["canonical_text"]
            requirement_display_ids[r["display_id"]] = r["display_id"]

        parse_table = ParseArtifact.__table__
        parse_row = (
            db.execute(
                select(parse_table.c.source_units_json)
                .where(parse_table.c.candidate_id == candidate_id)
                .order_by(parse_table.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        unit_locators: dict[str, dict | None] = {}
        if parse_row is not None:
            unit_locators = {unit["id"]: unit["locator"] for unit in parse_row["source_units_json"]}

        # Items referencing a requirement excluded above (unrecognized
        # Component) are dropped rather than crashing the whole endpoint —
        # same skip-and-log defense applied one step further.
        items = [item for item in items if str(item["job_requirement_id"]) in requirement_texts]

        rows = build_evidence_rows(items, requirement_texts, requirement_display_ids, requirement_components, unit_locators)

        # Story 6.3: real per-row Disputed review state, replacing the
        # constant "NoReviewFlag" placeholder Story 6.2 disclosed as
        # pending this story. At most one row per job_requirement_id by
        # the table's own unique index — a row's absence means the
        # implicit default (not disputed, version 0).
        reviews_table = EvidenceReview.__table__
        review_rows = (
            db.execute(
                select(
                    reviews_table.c.job_requirement_id,
                    reviews_table.c.disputed,
                    reviews_table.c.version,
                ).where(
                    (reviews_table.c.analysis_revision_id == revision_id)
                    & (reviews_table.c.candidate_id == candidate_id)
                )
            )
            .mappings()
            .all()
        )
        reviews_by_requirement = {r["job_requirement_id"]: r for r in review_rows}

        return {
            "candidate_id": candidate_id,
            "revision_number": revision_row["revision_number"],
            "is_current": is_current,
            "rows": [
                {
                    "job_requirement_id": row.job_requirement_id,
                    "requirement_display_id": row.requirement_display_id,
                    "requirement_text": row.requirement_text,
                    "state": row.state,
                    "locator_description": row.locator_description,
                    "excerpt": row.excerpt,
                    "review": _review_projection(reviews_by_requirement.get(row.job_requirement_id)),
                }
                for row in rows
            ],
        }
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        # AttributeError included (review finding): a non-Mapping locator
        # value in malformed source_units_json raises AttributeError from
        # _describe_locator's own .get() access, not KeyError/TypeError —
        # must still collapse to the neutral 404, not a 500.
        print(f"candidate_evidence: candidate {candidate_id} in revision {revision_id} — {exc}", file=sys.stderr)
        raise _NOT_FOUND


@router.get("/candidates/{candidate_id}/reconciliation")
def candidate_reconciliation(
    candidate_id: str,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 6.2 (AC#2; AR-8, AR-12, AR-21, AR-27-29): the authorized
    per-component score-reconciliation projection — base/effective weights,
    N/A redistribution, and the already-exact, already-largest-remainder-
    reconciled `component_contribution_display` (Story 4.5/4.6), reshaped
    per component. Only `NewResult`/`ReusedResult` qualify — Needs Review
    has no score to reconcile (AC#2's own "scoreable Candidate"
    precondition); anything else collapses into the same neutral
    `_NOT_FOUND` as every other malformed case in this module. No scoring
    is recomputed here — every value is already exact and persisted."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    candidates_table = Candidate.__table__
    revision_id = revision_row["id"]

    memberships_table = RevisionMembership.__table__
    results_table = CandidateResult.__table__
    member_row = (
        db.execute(
            select(
                memberships_table.c.outcome,
                results_table.c.component_contribution_display,
                results_table.c.precise_score_percent,
                results_table.c.headline_whole_percent,
            )
            .select_from(candidates_table)
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
            .where(candidates_table.c.id == candidate_id)
        )
        .mappings()
        .one_or_none()
    )
    if member_row is None or member_row["outcome"] not in ("NewResult", "ReusedResult"):
        raise _NOT_FOUND

    try:
        contribution_display = member_row["component_contribution_display"]
        if contribution_display is None:
            raise ValueError("scoreable membership has no component_contribution_display")

        scoring_table = ScoringConfiguration.__table__
        scoring_rows = (
            db.execute(
                select(scoring_table.c.component, scoring_table.c.applicable, scoring_table.c.effective_weight_bps)
                .where(scoring_table.c.analysis_session_id == candidate_row["analysis_session_id"])
            )
            .mappings()
            .all()
        )
        by_component: dict[Component, Mapping[str, Any]] = {}
        for r in scoring_rows:
            try:
                by_component[Component(r["component"])] = r
            except ValueError:
                print(
                    f"candidate_reconciliation: scoring_configurations has unrecognized component {r['component']!r} — excluded",
                    file=sys.stderr,
                )
                continue

        components: list[dict[str, Any]] = []
        for component in Component:
            row = by_component.get(component)
            applicable = bool(row["applicable"]) if row is not None else False
            effective_weight_percent = (
                str((Decimal(row["effective_weight_bps"]) / Decimal(100)).quantize(Decimal("0.01")))
                if applicable and row is not None
                else None
            )
            contribution = contribution_display.get(component.value) if applicable else None
            components.append(
                {
                    "component": component.value,
                    "base_weight_percent": str(
                        (Decimal(BASE_WEIGHT_BPS[component]) / Decimal(100)).quantize(Decimal("0.01"))
                    ),
                    "applicable": applicable,
                    "effective_weight_percent": effective_weight_percent,
                    "contribution_percent": str(contribution) if contribution is not None else None,
                }
            )

        return {
            "candidate_id": candidate_id,
            "revision_number": revision_row["revision_number"],
            "is_current": is_current,
            "components": components,
            # None-guard (review finding): a scoreable membership always
            # persists precise_score_percent alongside component_
            # contribution_display, but str(None) == "None" is a real,
            # silently-wrong wire value if that trust boundary is ever
            # violated — mirrors candidate_report's identical guard.
            "precise_score_percent": (
                str(member_row["precise_score_percent"]) if member_row["precise_score_percent"] is not None else None
            ),
            "headline_whole_percent": member_row["headline_whole_percent"],
        }
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        # AttributeError included (review finding): a non-dict persisted
        # component_contribution_display raises AttributeError from its
        # own .get() access, not KeyError/TypeError — must still collapse
        # to the neutral 404, not a 500.
        print(f"candidate_reconciliation: candidate {candidate_id} in revision {revision_id} — {exc}", file=sys.stderr)
        raise _NOT_FOUND


def _load_report_member_row(db: OrmSession, candidate_id: str, revision_id: str) -> Mapping[str, Any] | None:
    """The same per-Candidate report row `candidate_report`'s own query
    builds (Story 7.3 copies this query block rather than calling that
    endpoint over HTTP — the query itself is internal, no shared helper
    existed for it before this story), plus `component_contribution_display`,
    which `candidate_report` itself never needed but `candidate_print`'s
    reconciliation branch does."""
    candidates_table = Candidate.__table__
    documents_table = Document.__table__
    memberships_table = RevisionMembership.__table__
    identities_table = CandidateIdentity.__table__
    results_table = CandidateResult.__table__
    question_set_jobs_table = QuestionSetJob.__table__
    return (
        db.execute(
            select(
                candidates_table.c.document_reference,
                documents_table.c.original_filename,
                memberships_table.c.outcome,
                identities_table.c.display_name,
                results_table.c.id.label("result_id"),
                results_table.c.candidate_job_id,
                results_table.c.headline_whole_percent,
                results_table.c.precise_score_percent,
                results_table.c.component_contribution_display,
                results_table.c.gate_codes,
                question_set_jobs_table.c.status.label("question_set_job_status"),
                question_set_jobs_table.c.reclaim_count.label("question_set_reclaim_count"),
                question_set_jobs_table.c.failure_reason.label("question_set_failure_reason"),
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
            .join(
                question_set_jobs_table,
                (question_set_jobs_table.c.candidate_id == candidates_table.c.id)
                & (question_set_jobs_table.c.analysis_revision_id == revision_id),
                isouter=True,
            )
            .where(candidates_table.c.id == candidate_id)
        )
        .mappings()
        .one_or_none()
    )


def _build_print_metadata(
    db: OrmSession,
    candidate_row: Mapping[str, Any],
    revision_row: Mapping[str, Any],
    member_row: Mapping[str, Any],
) -> dict[str, Any]:
    """Shared by both print endpoints (Task 2): the confirmation-screen
    fields, computed once per request. Caller has already verified
    `member_row["outcome"]` is one of the three qualifying values."""
    display_name = _candidate_display_name(member_row["display_name"], member_row["document_reference"])
    outcome = "Ranked" if member_row["outcome"] in ("NewResult", "ReusedResult") else "NeedsReview"
    scope = derive_print_scope(outcome)

    question_set_state = ""
    if outcome == "Ranked":
        question_set_state = derive_question_set_state(
            member_row["question_set_job_status"],
            member_row["question_set_reclaim_count"] or 0,
            member_row["question_set_failure_reason"],
        )
    blocked = is_print_blocked(outcome, question_set_state)

    revision_number = revision_row["revision_number"]
    retried_document_reference: str | None = None
    if revision_number > 1:
        documents_table = Document.__table__
        # Code review fix (Edge Case Hunter/Blind Hunter, convergent): no DB
        # constraint enforces at most one Document per
        # retried_into_revision_id — `.limit(1)` (not `.one_or_none()`) means
        # a data-integrity violation of that business invariant degrades to
        # "some" trigger text instead of an uncaught MultipleResultsFound
        # 500, matching this module's "must never crash on a data-integrity
        # edge case" print-projection discipline.
        retried_row = (
            db.execute(
                select(documents_table.c.document_reference)
                .where(documents_table.c.retried_into_revision_id == revision_row["id"])
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        retried_document_reference = retried_row["document_reference"] if retried_row is not None else None
    trigger = derive_trigger(revision_number, retried_document_reference)

    metadata: dict[str, Any] = {
        "candidate_id": candidate_row["id"],
        "document_reference": member_row["document_reference"],
        "original_filename": member_row["original_filename"],
        "display_name": display_name,
        "outcome": outcome,
        "scope": scope,
        "revision_number": revision_number,
        "revision_created_at": revision_row["created_at"].isoformat(),
        "published_at": revision_row["published_at"].isoformat(),
        "trigger": trigger,
        "blocked": blocked,
    }
    if blocked:
        metadata["blocked_reason"] = "question_set_incomplete"
    return metadata


def _load_requirement_maps(
    db: OrmSession, analysis_session_id: str
) -> tuple[dict[str, str], dict[str, str], dict[str, Component]]:
    """`requirement_texts`, `requirement_display_ids`, `requirement_components`
    — the exact triple `candidate_evidence` already builds inline (copied,
    not imported: no shared query helper existed for it before this
    story)."""
    requirements_table = JobRequirement.__table__
    requirement_rows = (
        db.execute(
            select(
                requirements_table.c.id,
                requirements_table.c.display_id,
                requirements_table.c.canonical_text,
                requirements_table.c.component,
            ).where(requirements_table.c.analysis_session_id == analysis_session_id)
        )
        .mappings()
        .all()
    )
    requirement_texts: dict[str, str] = {}
    requirement_display_ids: dict[str, str] = {}
    requirement_components: dict[str, Component] = {}
    for r in requirement_rows:
        # Keyed by display_id, not the row's own UUID `id` — see
        # candidate_finalizer.py's _assemble_scoreability_inputs for why
        # (same fix, same bug, every inline copy of this pattern).
        try:
            requirement_components[r["display_id"]] = Component(r["component"])
        except ValueError:
            print(
                f"candidate_print: job_requirement {r['id']} has unrecognized component {r['component']!r} — excluded from Evidence selection",
                file=sys.stderr,
            )
            continue
        requirement_texts[r["display_id"]] = r["canonical_text"]
        requirement_display_ids[r["display_id"]] = r["display_id"]
    return requirement_texts, requirement_display_ids, requirement_components


def _load_evidence_payload(
    db: OrmSession,
    candidate_id: str,
    revision_id: str,
    candidate_job_id: str | None,
    requirement_texts: Mapping[str, str],
    requirement_display_ids: Mapping[str, str],
    requirement_components: Mapping[str, Component],
) -> list[dict[str, Any]]:
    """The exact `candidate_evidence` row shape (locator/excerpt/review),
    copied rather than called over HTTP — same reasoning as
    `_load_requirement_maps`."""
    proposals_table = CandidateProposal.__table__
    items: list[dict] = []
    if candidate_job_id is not None:
        proposal_row = (
            db.execute(
                select(proposals_table.c.items_json).where(proposals_table.c.candidate_job_id == candidate_job_id)
            )
            .mappings()
            .one_or_none()
        )
        items = proposal_row["items_json"] if proposal_row is not None else []

    parse_table = ParseArtifact.__table__
    parse_row = (
        db.execute(
            select(parse_table.c.source_units_json)
            .where(parse_table.c.candidate_id == candidate_id)
            .order_by(parse_table.c.created_at.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    unit_locators: dict[str, dict | None] = {}
    if parse_row is not None:
        unit_locators = {unit["id"]: unit["locator"] for unit in parse_row["source_units_json"]}

    items = [item for item in items if str(item["job_requirement_id"]) in requirement_texts]
    rows = build_evidence_rows(items, requirement_texts, requirement_display_ids, requirement_components, unit_locators)

    reviews_table = EvidenceReview.__table__
    review_rows = (
        db.execute(
            select(
                reviews_table.c.job_requirement_id,
                reviews_table.c.disputed,
                reviews_table.c.version,
            ).where((reviews_table.c.analysis_revision_id == revision_id) & (reviews_table.c.candidate_id == candidate_id))
        )
        .mappings()
        .all()
    )
    reviews_by_requirement = {r["job_requirement_id"]: r for r in review_rows}

    return [
        {
            "job_requirement_id": row.job_requirement_id,
            "requirement_display_id": row.requirement_display_id,
            "requirement_text": row.requirement_text,
            "state": row.state,
            "locator_description": row.locator_description,
            "excerpt": row.excerpt,
            "review": _review_projection(reviews_by_requirement.get(row.job_requirement_id)),
        }
        for row in rows
    ]


def _load_reconciliation_payload(
    db: OrmSession,
    analysis_session_id: str,
    contribution_display: Mapping[str, Any],
    precise_score_percent: Any,
    headline_whole_percent: int,
) -> dict[str, Any]:
    """The exact `candidate_reconciliation` component-reshaping block,
    copied rather than called over HTTP — same reasoning as
    `_load_requirement_maps`."""
    scoring_table = ScoringConfiguration.__table__
    scoring_rows = (
        db.execute(
            select(scoring_table.c.component, scoring_table.c.applicable, scoring_table.c.effective_weight_bps).where(
                scoring_table.c.analysis_session_id == analysis_session_id
            )
        )
        .mappings()
        .all()
    )
    by_component: dict[Component, Mapping[str, Any]] = {}
    for r in scoring_rows:
        try:
            by_component[Component(r["component"])] = r
        except ValueError:
            print(
                f"candidate_print: scoring_configurations has unrecognized component {r['component']!r} — excluded",
                file=sys.stderr,
            )
            continue

    components: list[dict[str, Any]] = []
    for component in Component:
        row = by_component.get(component)
        applicable = bool(row["applicable"]) if row is not None else False
        effective_weight_percent = (
            str((Decimal(row["effective_weight_bps"]) / Decimal(100)).quantize(Decimal("0.01")))
            if applicable and row is not None
            else None
        )
        contribution = contribution_display.get(component.value) if applicable else None
        components.append(
            {
                "component": component.value,
                "base_weight_percent": str((Decimal(BASE_WEIGHT_BPS[component]) / Decimal(100)).quantize(Decimal("0.01"))),
                "applicable": applicable,
                "effective_weight_percent": effective_weight_percent,
                "contribution_percent": str(contribution) if contribution is not None else None,
            }
        )
    return {
        "components": components,
        "precise_score_percent": str(precise_score_percent) if precise_score_percent is not None else None,
        "headline_whole_percent": headline_whole_percent,
    }


def _load_questions_payload(
    db: OrmSession, candidate_id: str, revision_id: str, requirement_display_ids: Mapping[str, str]
) -> list[dict[str, Any]]:
    """The current published Interview Question Set version's items,
    reshaped for print. `question_set_jobs.status == "published"` is the
    "current complete version" signal (Story 7.2's coordinator is the only
    writer of `question_set_versions`, and `published` never revisits —
    see that story's Dev Notes on why `version` is always 1 in V1). A
    non-empty `source_requirement_id` is remapped to the matching
    `requirement_display_id`; an empty one (a `gap_focused` item with no
    single grounding requirement, per `QuestionItem`'s contract) is left
    unchanged since it is not a key into `requirement_display_ids` at all."""
    jobs_table = QuestionSetJob.__table__
    versions_table = QuestionSetVersion.__table__
    row = (
        db.execute(
            select(versions_table.c.items_json)
            .select_from(jobs_table)
            .join(versions_table, versions_table.c.question_set_job_id == jobs_table.c.id)
            .where(jobs_table.c.candidate_id == candidate_id)
            .where(jobs_table.c.analysis_revision_id == revision_id)
            .where(jobs_table.c.status == "published")
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return []
    items = sorted(row["items_json"], key=lambda item: item["number"])
    questions: list[dict[str, Any]] = []
    for item in items:
        source_requirement_id = str(item.get("source_requirement_id") or "")
        questions.append(
            {
                "number": item["number"],
                "category": item["category"],
                "text": item["text"],
                "source_requirement_id": requirement_display_ids.get(source_requirement_id, source_requirement_id),
            }
        )
    return questions


@router.get("/candidates/{candidate_id}/print/prepare")
def candidate_print_prepare(
    candidate_id: str,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 7.3 (AC#1, AC#2, AC#4; AD-3, AD-18, AR-8, AR-43): the print
    confirmation-screen projection. Independently re-authorizes via
    `_authorize_candidate_revision` — the same neutral `_NOT_FOUND` on any
    miss every other endpoint in this module uses. Returns metadata only
    (no Evidence/reconciliation/questions) — `GET .../print` is the full
    DTO. Repeated calls are trivially read-idempotent (AC#4): a published
    revision's membership/result/Question Set never change, so this
    function has no side effect to be idempotent about in the first place."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    member_row = _load_report_member_row(db, candidate_id, revision_row["id"])
    if member_row is None or member_row["outcome"] not in ("NewResult", "ReusedResult", "NeedsReview"):
        raise _NOT_FOUND
    try:
        metadata = _build_print_metadata(db, candidate_row, revision_row, member_row)
        return {**metadata, "notice": dict(RESPONSIBLE_HIRING_NOTICE)}
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        print(
            f"candidate_print_prepare: candidate {candidate_id} in revision {revision_row['id']} — {exc}",
            file=sys.stderr,
        )
        raise _NOT_FOUND


@router.get("/candidates/{candidate_id}/print")
def candidate_print(
    candidate_id: str,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 7.3 (AC#1, AC#2, AC#3, AC#4; AD-8, AD-18, AR-8, AR-43): the
    full authorized static print DTO — the first endpoint in this codebase
    to return published Interview Question text to a client. Independently
    re-authorizes exactly like `candidate_print_prepare` above (AC#4: a
    separate request gets a separate authorization check, never a cached
    result from `prepare`). A blocked scored Candidate (Question Set not
    yet `Complete`) returns the same metadata-only shape `prepare` already
    returns rather than a partial Evidence/reconciliation/questions payload
    — AD-8's "partial proposal is never visible" applies here too: a print
    payload is either complete or withheld entirely, never half-built."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    member_row = _load_report_member_row(db, candidate_id, revision_row["id"])
    if member_row is None or member_row["outcome"] not in ("NewResult", "ReusedResult", "NeedsReview"):
        raise _NOT_FOUND
    try:
        metadata = _build_print_metadata(db, candidate_row, revision_row, member_row)
        payload: dict[str, Any] = {**metadata, "notice": dict(RESPONSIBLE_HIRING_NOTICE)}
        if metadata["blocked"]:
            return payload

        requirement_texts, requirement_display_ids, requirement_components = _load_requirement_maps(
            db, candidate_row["analysis_session_id"]
        )
        payload["evidence"] = _load_evidence_payload(
            db,
            candidate_id,
            revision_row["id"],
            member_row["candidate_job_id"],
            requirement_texts,
            requirement_display_ids,
            requirement_components,
        )

        if metadata["scope"] == SCORED_COMBINED:
            if member_row["result_id"] is None:
                raise ValueError("scoreable membership has no matching candidate_results row")
            contribution_display = member_row["component_contribution_display"]
            if contribution_display is None:
                raise ValueError("scoreable membership has no component_contribution_display")
            payload["reconciliation"] = _load_reconciliation_payload(
                db,
                candidate_row["analysis_session_id"],
                contribution_display,
                member_row["precise_score_percent"],
                member_row["headline_whole_percent"],
            )
            payload["questions"] = _load_questions_payload(
                db, candidate_id, revision_row["id"], requirement_display_ids
            )
        else:
            payload["gate_codes"] = member_row["gate_codes"] or []

        return payload
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        print(f"candidate_print: candidate {candidate_id} in revision {revision_row['id']} — {exc}", file=sys.stderr)
        raise _NOT_FOUND


class EvidenceReviewRequest(BaseModel):
    disputed: bool
    # Review finding (Edge Case Hunter): an unbounded expected_version
    # reaches the CAS UPDATE's Postgres Integer bind param — out-of-INT32
    # range raised an uncaught DataError (500) instead of a clean 4xx.
    # Bounded the same way revision_number already is on this endpoint.
    expected_version: int = Field(ge=0, le=2147483647)
    idempotency_key: str = Field(min_length=1, max_length=128)


@router.put("/candidates/{candidate_id}/evidence/{job_requirement_id}/review")
def evidence_review(
    candidate_id: str,
    job_requirement_id: str,
    body: EvidenceReviewRequest,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 6.3 (AC#1, AC#3; AR-7-8, AR-32, AR-34): mark/clear the
    Disputed review flag on one Evidence row, scoped to one Analysis
    Revision. Never mutates classification, Evidence, score, rank, or any
    other revision's review state — `evidence_reviews`' own unique key
    (`analysis_revision_id`, `candidate_id`, `job_requirement_id`)
    guarantees that structurally, not application logic alone. Same
    two-hop ownership + outcome qualification `candidate_evidence` uses,
    plus an explicit "does this Evidence row actually exist for this
    Candidate" check (AR-32: the actor must independently own the
    Evidence row too, not just the Candidate/revision) — a Requirement
    with no evaluated proposal item (Needs Review's empty `items_json`,
    or a requirement id from a different session) has nothing to mark
    Disputed and collapses to the same neutral 404 as any other
    ownership miss. CAS/idempotency shape mirrors `document_upload.py`'s
    `remove_document`/`replace_document` verbatim — the only prior
    precedent for this mutation pattern in this codebase."""
    if len(job_requirement_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND

    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    candidates_table = Candidate.__table__
    revision_id = revision_row["id"]

    memberships_table = RevisionMembership.__table__
    results_table = CandidateResult.__table__
    member_row = (
        db.execute(
            select(
                memberships_table.c.outcome,
                results_table.c.candidate_job_id,
            )
            .select_from(candidates_table)
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
            .where(candidates_table.c.id == candidate_id)
        )
        .mappings()
        .one_or_none()
    )
    if member_row is None or member_row["outcome"] not in ("NewResult", "ReusedResult", "NeedsReview"):
        raise _NOT_FOUND

    try:
        proposals_table = CandidateProposal.__table__
        items: list[dict] = []
        if member_row["candidate_job_id"] is not None:
            proposal_row = (
                db.execute(
                    select(proposals_table.c.items_json).where(
                        proposals_table.c.candidate_job_id == member_row["candidate_job_id"]
                    )
                )
                .mappings()
                .one_or_none()
            )
            items = proposal_row["items_json"] if proposal_row is not None else []

        # AR-32: the Evidence row itself must exist for this Candidate.
        if not any(str(item.get("job_requirement_id")) == job_requirement_id for item in items):
            raise _NOT_FOUND

        reviews_table = EvidenceReview.__table__
        existing = (
            db.execute(
                select(reviews_table).where(
                    (reviews_table.c.analysis_revision_id == revision_id)
                    & (reviews_table.c.candidate_id == candidate_id)
                    & (reviews_table.c.job_requirement_id == job_requirement_id)
                )
            )
            .mappings()
            .one_or_none()
        )

        if existing is None:
            if body.disputed is False:
                # Clearing an already-clear flag is an idempotent no-op —
                # never materialize a row for "no change" (Dev Notes).
                # Review finding (Blind Hunter, TOCTOU): the row this
                # branch's caller read may have been created by a
                # concurrent first-set between that read and this
                # response. Re-check immediately before responding so a
                # 200 here can never contradict what is actually
                # persisted a moment later — narrows the race window to
                # the theoretical minimum without adding lock
                # infrastructure (ponytail: a single-Recruiter-per-session
                # V1 demo tool has no realistic double-tab race on the
                # same Evidence row; full elimination would need an
                # advisory lock or restructuring the "no row" state into
                # a real UPSERT, upgrade path if that scale ever matters).
                current = (
                    db.execute(
                        select(reviews_table).where(
                            (reviews_table.c.analysis_revision_id == revision_id)
                            & (reviews_table.c.candidate_id == candidate_id)
                            & (reviews_table.c.job_requirement_id == job_requirement_id)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    return {"job_requirement_id": job_requirement_id, "disputed": False, "version": 0}
                raise HTTPException(
                    status_code=409,
                    detail={
                        "job_requirement_id": job_requirement_id,
                        "disputed": current["disputed"],
                        "version": current["version"],
                    },
                )
            if body.expected_version != 0:
                raise HTTPException(
                    status_code=409,
                    detail={"job_requirement_id": job_requirement_id, "disputed": False, "version": 0},
                )
            now = datetime.now(timezone.utc)
            insert_stmt = reviews_table.insert().values(
                id=str(uuid.uuid4()),
                analysis_revision_id=revision_id,
                candidate_id=candidate_id,
                job_requirement_id=job_requirement_id,
                disputed=True,
                version=1,
                last_command_idempotency_key=body.idempotency_key,
                created_at=now,
                updated_at=now,
            )
            try:
                db.execute(insert_stmt)
                db.commit()
            except IntegrityError:
                # A concurrent identical first-set request won the race —
                # re-read and fall through to the existing-row branch
                # below, which resolves replay vs. genuine conflict.
                db.rollback()
                existing = (
                    db.execute(
                        select(reviews_table).where(
                            (reviews_table.c.analysis_revision_id == revision_id)
                            & (reviews_table.c.candidate_id == candidate_id)
                            & (reviews_table.c.job_requirement_id == job_requirement_id)
                        )
                    )
                    .mappings()
                    .one()
                )
            else:
                return {"job_requirement_id": job_requirement_id, "disputed": True, "version": 1}

        # Existing row (found originally, or lost the insert race above):
        # idempotent replay short-circuits before the CAS (mirrors
        # remove_document's exact pattern).
        if existing["last_command_idempotency_key"] == body.idempotency_key:
            if existing["disputed"] == body.disputed:
                return {
                    "job_requirement_id": job_requirement_id,
                    "disputed": existing["disputed"],
                    "version": existing["version"],
                }
            # Review finding (Edge Case Hunter): the same idempotency_key
            # reused for a DIFFERENT disputed value is not a replay of an
            # earlier command — it's a genuine idempotency-guarantee
            # violation (unlike document_upload.py's remove/replace,
            # whose target value is a fixed constant, this endpoint's
            # `disputed` varies, so "same key" alone can't imply "same
            # command"). Reject rather than silently applying it as a new
            # mutation under a reused key.
            raise HTTPException(
                status_code=409,
                detail={
                    "job_requirement_id": job_requirement_id,
                    "disputed": existing["disputed"],
                    "version": existing["version"],
                },
            )

        result = db.execute(
            reviews_table.update()
            .where(reviews_table.c.id == existing["id"])
            .where(reviews_table.c.version == body.expected_version)
            .values(
                disputed=body.disputed,
                version=reviews_table.c.version + 1,
                last_command_idempotency_key=body.idempotency_key,
                updated_at=datetime.now(timezone.utc),
            )
        )
        if result.rowcount == 0:
            db.rollback()
            current = db.execute(select(reviews_table).where(reviews_table.c.id == existing["id"])).mappings().one()
            if current["last_command_idempotency_key"] == body.idempotency_key and current["disputed"] == body.disputed:
                return {
                    "job_requirement_id": job_requirement_id,
                    "disputed": current["disputed"],
                    "version": current["version"],
                }
            raise HTTPException(
                status_code=409,
                detail={
                    "job_requirement_id": job_requirement_id,
                    "disputed": current["disputed"],
                    "version": current["version"],
                },
            )

        db.commit()
        updated = db.execute(select(reviews_table).where(reviews_table.c.id == existing["id"])).mappings().one()
        return {"job_requirement_id": job_requirement_id, "disputed": updated["disputed"], "version": updated["version"]}
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        db.rollback()
        print(
            f"evidence_review: candidate {candidate_id} requirement {job_requirement_id} in revision {revision_id} — {exc}",
            file=sys.stderr,
        )
        raise _NOT_FOUND


class ShortlistRequest(BaseModel):
    state: Literal["Shortlisted", "NotShortlisted"]
    expected_version: int = Field(ge=1, le=2147483647)
    idempotency_key: str = Field(min_length=1, max_length=128)


@router.put("/candidates/{candidate_id}/shortlist")
def candidate_shortlist(
    candidate_id: str,
    body: ShortlistRequest,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    """Story 6.4 (AC#1-2; AR-7-8, AR-34): toggle the Candidate-owned
    Shortlist state. `_authorize_candidate_revision` reuse gives the same
    two-hop ownership check (AR-8) every other Candidate mutation in this
    module uses; `revision_number` is authorization plumbing only — unlike
    `evidence_review`, `shortlists` carries no revision column and is
    never scoped or filtered by it (Shortlist is Candidate-owned, not
    revision-owned). No outcome-qualification gate either (unlike
    `evidence_review`'s NewResult/ReusedResult/NeedsReview check): AC#3
    requires Shortlist to remain settable/readable even after a retry
    moves a Candidate to Failed, so this endpoint must stay reachable
    regardless of outcome. CAS/idempotency shape mirrors `evidence_review`
    exactly, including its reused-key-with-a-different-value guard —
    `state` varies per request the same way `disputed` does, so a matching
    idempotency_key alone can never imply the same command."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    shortlists_table = Shortlist.__table__

    try:
        existing = (
            db.execute(select(shortlists_table).where(shortlists_table.c.candidate_id == candidate_id))
            .mappings()
            .one_or_none()
        )
        # Defended, not expected: every Candidate that can reach a
        # published revision (a precondition of _authorize_candidate_
        # revision succeeding above) has already been finalized at least
        # once, and candidate_finalizer.py default-inserts a shortlists
        # row on every outcome branch before that happens.
        if existing is None:
            raise _NOT_FOUND

        if existing["last_command_idempotency_key"] == body.idempotency_key:
            if existing["state"] == body.state:
                return {"state": existing["state"], "version": existing["version"]}
            raise HTTPException(
                status_code=409,
                detail={"state": existing["state"], "version": existing["version"]},
            )

        result = db.execute(
            shortlists_table.update()
            .where(shortlists_table.c.id == existing["id"])
            .where(shortlists_table.c.version == body.expected_version)
            .values(
                state=body.state,
                version=shortlists_table.c.version + 1,
                last_command_idempotency_key=body.idempotency_key,
                updated_at=datetime.now(timezone.utc),
            )
        )
        if result.rowcount == 0:
            db.rollback()
            current = (
                db.execute(select(shortlists_table).where(shortlists_table.c.id == existing["id"])).mappings().one()
            )
            if current["last_command_idempotency_key"] == body.idempotency_key and current["state"] == body.state:
                return {"state": current["state"], "version": current["version"]}
            raise HTTPException(
                status_code=409,
                detail={"state": current["state"], "version": current["version"]},
            )

        db.commit()
        updated = db.execute(select(shortlists_table).where(shortlists_table.c.id == existing["id"])).mappings().one()
        return {"state": updated["state"], "version": updated["version"]}
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        db.rollback()
        print(f"candidate_shortlist: candidate {candidate_id} — {exc}", file=sys.stderr)
        raise _NOT_FOUND


class QuestionSetRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


@router.post("/candidates/{candidate_id}/question-set")
def candidate_question_set(
    candidate_id: str,
    body: QuestionSetRequest,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> Any:
    """Story 7.1 (AC#1, AC#3; AR-8, AR-34): idempotently creates or returns
    the one Interview Question Set generation job for this (Candidate,
    published Analysis Revision) pair. Only a successful
    (`NewResult`/`ReusedResult`) Candidate Result is eligible (AR-34's
    "completed Candidate Result"); `NeedsReview`/`Failed` and any
    ownership/validation miss collapse into the same neutral 404 (AC#3).

    Code review fix (Acceptance Auditor, High): an earlier version keyed
    the job on `candidate_id` alone and used a `new_analysis.py::analyze`-
    style key+fingerprint three-way branch to distinguish "replay" from
    "conflicting second attempt." That was solving a problem this endpoint
    doesn't actually have: `question_set_jobs` is now unique on
    `(candidate_id, analysis_revision_id)` (migration `d2e3f4a5b6c7`), so
    the natural key already fully identifies the command's target — there
    is no second, materially-different request `idempotency_key` could ever
    need to distinguish for the identical (Candidate, revision) pair.
    "Requested once, duplicated, or refreshed" (AC#1) all collapse to the
    same answer: does a row for this (candidate_id, revision_id) exist yet?
    `idempotency_key` is still required and stored (matches this codebase's
    per-command convention) but no longer drives branching logic."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    revision_id = revision_row["id"]

    memberships_table = RevisionMembership.__table__
    membership_row = (
        db.execute(
            select(memberships_table.c.outcome)
            .where(memberships_table.c.candidate_id == candidate_id)
            .where(memberships_table.c.analysis_revision_id == revision_id)
        )
        .mappings()
        .one_or_none()
    )
    if membership_row is None or membership_row["outcome"] not in ("NewResult", "ReusedResult"):
        raise _NOT_FOUND

    jobs_table = QuestionSetJob.__table__

    try:
        existing = (
            db.execute(
                select(jobs_table.c.status, jobs_table.c.reclaim_count, jobs_table.c.failure_reason)
                .where(jobs_table.c.candidate_id == candidate_id)
                .where(jobs_table.c.analysis_revision_id == revision_id)
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            # AC#1: requested once, duplicated, or refreshed all return the
            # one existing job's current real state. Review fix (Blind
            # Hunter, High): this used to hardcode "Generating" regardless
            # of actual status — harmless in 7.1 (only two states existed),
            # but after 7.2 introduced Complete/Failed/Recovering/Retrying,
            # a duplicate POST against a since-published or since-failed job
            # would misreport "Generating" forever (masking a Failed state
            # behind a permanently-stale label until reload). Now uses the
            # same projection the report and retry endpoint already use.
            state = derive_question_set_state(
                existing["status"], existing["reclaim_count"] or 0, existing["failure_reason"]
            )
            return JSONResponse(status_code=202, content={"question_set_state": state})

        new_id = str(uuid.uuid4())
        try:
            db.execute(
                jobs_table.insert().values(
                    id=new_id,
                    candidate_id=candidate_id,
                    analysis_revision_id=revision_id,
                    status="queued",
                    idempotency_key=body.idempotency_key,
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
        except IntegrityError:
            # A concurrent first-request won the unique-(candidate_id,
            # analysis_revision_id) race — same recovery shape
            # evidence_review's own first-insert race already established.
            # `.one_or_none()` (code review fix, not `.one()`): defended,
            # not expected, against the winner row vanishing between the
            # failed insert and this re-read — falls through to the neutral
            # 404 below rather than letting NoResultFound escape as a 500.
            db.rollback()
            winner = (
                db.execute(
                    select(jobs_table.c.id)
                    .where(jobs_table.c.candidate_id == candidate_id)
                    .where(jobs_table.c.analysis_revision_id == revision_id)
                )
                .mappings()
                .one_or_none()
            )
            if winner is None:
                raise _NOT_FOUND
            return JSONResponse(status_code=202, content={"question_set_state": "Generating"})
        return JSONResponse(status_code=202, content={"question_set_state": "Generating"})
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        db.rollback()
        print(f"candidate_question_set: candidate {candidate_id} — {exc}", file=sys.stderr)
        raise _NOT_FOUND


class QuestionSetRetryRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


@router.post("/candidates/{candidate_id}/question-set/retry")
def candidate_question_set_retry(
    candidate_id: str,
    body: QuestionSetRetryRequest,
    revision_number: int | None = Query(default=None, ge=1, le=2147483647),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> Any:
    """Story 7.2 (AC#3; AR-8, AR-34): the isolated retry command — restarts
    an exhausted `question_set_jobs` row (`status='failed'`, whether from
    the worker's own attempt exhaustion or the coordinator's
    `'incomplete_proposal'` rejection) at a fresh `attempt=1` cycle. Only
    ever touches `question_set_jobs`/`question_set_proposals` for this one
    Candidate — never `candidate_results`, `revision_memberships`,
    `evidence_reviews`, or `shortlists` ("isolated": a downstream Question
    Set failure never mutates Candidate analysis, and this command is a
    completely separate code path from `retry_revision.py`'s Candidate-level
    retry).

    Same two-hop ownership check and eligibility gate as
    `candidate_question_set` (only a successful `NewResult`/`ReusedResult`
    membership may retry). A missing job, or a job not in `status='failed'`,
    is not an error: AC#3's "duplicate commands... are idempotent" means a
    retry click against a job that is already in flight or already
    published returns the job's current real state via
    `question_set_projection.derive_question_set_state`, never a `409` —
    mirrors `candidate_question_set`'s own choice to fold duplicates into a
    `202` rather than surface a conflict."""
    candidate_row, revision_row, is_current = _authorize_candidate_revision(
        db, identity, candidate_id, revision_number
    )
    revision_id = revision_row["id"]

    memberships_table = RevisionMembership.__table__
    membership_row = (
        db.execute(
            select(memberships_table.c.outcome)
            .where(memberships_table.c.candidate_id == candidate_id)
            .where(memberships_table.c.analysis_revision_id == revision_id)
        )
        .mappings()
        .one_or_none()
    )
    if membership_row is None or membership_row["outcome"] not in ("NewResult", "ReusedResult"):
        raise _NOT_FOUND

    jobs_table = QuestionSetJob.__table__
    proposals_table = QuestionSetProposal.__table__

    try:
        existing = (
            db.execute(
                select(jobs_table)
                .where(jobs_table.c.candidate_id == candidate_id)
                .where(jobs_table.c.analysis_revision_id == revision_id)
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            raise _NOT_FOUND

        if existing["status"] not in ("failed", "unrecoverable"):
            state = derive_question_set_state(
                existing["status"], existing["reclaim_count"] or 0, existing["failure_reason"]
            )
            return JSONResponse(status_code=202, content={"question_set_state": state})

        # Review fix (Blind Hunter/Edge Case Hunter, convergent Medium):
        # accepts 'unrecoverable' (question_finalizer.py::_unstick_job's
        # data-integrity-edge-case terminal status) as well as 'failed' —
        # both project as the same public "Failed" state, so a Recruiter
        # sees an active Retry button for either; previously only 'failed'
        # was accepted, meaning an 'unrecoverable' job's Retry click
        # silently no-op'd (202 with the unchanged current state, no error
        # surfaced). In practice 'unrecoverable' requires either an orphaned
        # candidate/session (which independently makes the Report itself
        # unreachable via _authorize_candidate_revision) or a stale
        # membership outcome (which independently removes question_set_state
        # from the report's projection entirely) — so this is defensive
        # depth against those invariants ever weakening, not evidence they
        # are broken today.
        #
        # A stale proposal may exist from a prior 'completed' attempt the
        # coordinator rejected (question_finalizer.py's
        # 'incomplete_proposal' branch) — question_set_proposals is unique
        # on question_set_job_id, and the worker's stage_success uses
        # ON CONFLICT DO NOTHING, which would silently swallow the next
        # attempt's proposal if the old rejected one were left in place. It
        # was never published (no question_set_versions row references it),
        # so discarding it here is safe and necessary for the retry to
        # actually produce a new attempt.
        db.execute(proposals_table.delete().where(proposals_table.c.question_set_job_id == existing["id"]))

        job_cas = db.execute(
            jobs_table.update()
            .where(jobs_table.c.id == existing["id"])
            .where(jobs_table.c.status.in_(("failed", "unrecoverable")))
            .values(
                status="queued",
                attempt=1,
                reclaim_count=0,
                failure_reason=None,
                lease_token=None,
                lease_expires_at=None,
                generation=jobs_table.c.generation + 1,
                state_version=jobs_table.c.state_version + 1,
            )
        )
        if job_cas.rowcount != 1:
            # Concurrent retry already won (or, in principle, the
            # coordinator racing to finalize a stale failure — not
            # reachable since 'failed' is terminal for the coordinator, but
            # defended).
            db.rollback()
            current = (
                db.execute(select(jobs_table).where(jobs_table.c.id == existing["id"])).mappings().one_or_none()
            )
            if current is None:
                raise _NOT_FOUND
            state = derive_question_set_state(
                current["status"], current["reclaim_count"] or 0, current["failure_reason"]
            )
            return JSONResponse(status_code=202, content={"question_set_state": state})

        db.commit()
        return JSONResponse(status_code=202, content={"question_set_state": "Retrying"})
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        db.rollback()
        print(f"candidate_question_set_retry: candidate {candidate_id} — {exc}", file=sys.stderr)
        raise _NOT_FOUND
