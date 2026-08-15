"""Gateway Candidate finalizer coordinator (Story 4.6, AR-17, AR-18, AR-19):
a stateless, level-triggered scan that claims one terminal `candidate_jobs`
row (`status IN ('completed', 'failed')`), reauthorizes, wires Story 4.5's
pure `evaluate_candidate_scoreability` kernel to real PostgreSQL rows (or
maps an exhausted failure to one of AR-40's five frozen public categories),
and atomically appends a Candidate Result Version, moves
`revision_memberships` monotonically, completes `candidate_jobs`
bookkeeping, defaults Shortlist, and bumps the revision's publication
`requested_version`. No lease/token of its own — `'completed'`/`'failed'`
sit outside both the worker's `claim_queued` (`'queued'`) and the gateway's
own `recovery_sweep.py` `_LEASED_TABLES` (`'claimed'`, `'parsed'`), so a
short transaction-scoped `FOR UPDATE SKIP LOCKED` claim is the whole
reauthorization mechanism, mirroring `preparation_finalizer.py`.

AC#2 (retryable failure before attempt exhaustion) requires no branch here
at all: a mid-retry job sits at `status='queued'` (the worker's own
`stage_provider_failure`/`stage_parse_failure` already requeue it there),
a status this coordinator's claim query never selects.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as OrmSession

from ..domain.failure_category import map_failure_reason_to_category
from ..domain.scoring import evaluate_candidate_scoreability
from ..domain.scoring_configuration import Component
from .models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateJob,
    CandidateProposal,
    CandidateResult,
    JobRequirement,
    ParseArtifact,
    RevisionMembership,
    ScoringConfiguration,
    Shortlist,
)

_CLAIMABLE_STATUSES = ("completed", "failed")


def _assemble_scoreability_inputs(db: OrmSession, job: dict, candidate: dict):
    """Reads real `job_requirements`/`scoring_configurations`/
    `parse_artifacts`/`candidate_proposals` rows and shapes them into
    `evaluate_candidate_scoreability`'s plain-dict/list parameters (Story
    4.5's kernel is unmodified — this is new integration code only)."""
    requirements_table = JobRequirement.__table__
    requirement_rows = (
        db.execute(select(requirements_table).where(requirements_table.c.analysis_session_id == candidate["analysis_session_id"]))
        .mappings()
        .all()
    )
    # Keyed by display_id ("JR-001"), not the row's own UUID `id`: the
    # worker only ever knows/sends back display_id as `job_requirement_id`
    # (main.py's _fetch_job_requirements — "the exact id the provider
    # adapter's user message already sends/expects") — confirmed live, this
    # mismatch crashed every finalize attempt with "proposal_items does not
    # exactly match requirement_components" the first time a Candidate
    # proposal ever made it this far. Same fix applied at every other
    # inline copy of this pattern in workspace.py below.
    requirement_components = {row["display_id"]: Component(row["component"]) for row in requirement_rows}

    scoring_table = ScoringConfiguration.__table__
    scoring_rows = (
        db.execute(
            select(scoring_table)
            .where(scoring_table.c.analysis_session_id == candidate["analysis_session_id"])
            .where(scoring_table.c.applicable.is_(True))
        )
        .mappings()
        .all()
    )
    effective_weights = {Component(row["component"]): row["effective_weight_bps"] for row in scoring_rows}

    parse_table = ParseArtifact.__table__
    parse_row = (
        db.execute(
            select(parse_table)
            .where(parse_table.c.candidate_id == job["candidate_id"])
            .order_by(parse_table.c.created_at.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    parse_gate_codes = parse_row["gate_codes"] if parse_row is not None else []

    proposal_table = CandidateProposal.__table__
    proposal_row = (
        db.execute(select(proposal_table).where(proposal_table.c.candidate_job_id == job["id"])).mappings().one_or_none()
    )
    proposal_gate_codes = proposal_row["gate_codes"] if proposal_row is not None else []
    proposal_items = proposal_row["items_json"] if proposal_row is not None else []

    return dict(
        parse_gate_codes=parse_gate_codes,
        proposal_gate_codes=proposal_gate_codes,
        proposal_items=proposal_items,
        requirement_components=requirement_components,
        effective_weights=effective_weights,
        candidate_key=job["candidate_id"],
    )


def _fraction_columns(value: Fraction | None) -> tuple[object | None, object | None]:
    if value is None:
        return None, None
    return value.numerator, value.denominator


def _unstick_job(db: OrmSession, candidate_job_id: str, claimed_status: str, reason: str) -> None:
    """CAS `candidate_jobs.status` straight to `'finalized'` with no
    matching `CandidateResult` row, so a permanently unresolvable claim
    (orphaned candidate/session, a missing membership row, or a membership
    already terminalized out of band) leaves the claim pool instead of
    being reclaimed on every subsequent scan. Without this, the claim
    query's `ORDER BY created_at LIMIT 1` guarantees the same stuck row is
    reclaimed first forever, starving every legitimately-completed job
    behind it (review finding: independently identified by two review
    layers as head-of-line blocking). Callers must `db.rollback()` first
    to discard the aborted attempt's locks before calling this."""
    jobs_table = CandidateJob.__table__
    db.execute(
        jobs_table.update()
        .where(jobs_table.c.id == candidate_job_id)
        .where(jobs_table.c.status == claimed_status)
        .values(status="finalized")
    )
    db.commit()
    print(f"candidate job {candidate_job_id}: unstuck without a result — {reason}", file=sys.stderr)


def scan_and_finalize_candidates(db: OrmSession) -> bool:
    """Claims and finalizes at most one terminal Candidate job. Returns True
    if a row was claimed (whether or not it advanced further), False if
    there was nothing to do."""
    jobs_table = CandidateJob.__table__
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
    candidate_job_id = job["id"]
    candidate_id = job["candidate_id"]
    analysis_revision_id = job["analysis_revision_id"]
    claimed_status = job["status"]

    candidates_table = Candidate.__table__
    candidate_row = (
        db.execute(select(candidates_table).where(candidates_table.c.id == candidate_id)).mappings().one_or_none()
    )
    if candidate_row is None:
        db.rollback()
        _unstick_job(db, candidate_job_id, claimed_status, "orphaned candidate_id — no matching candidates row")
        return True
    candidate = dict(candidate_row)

    # Reauthorize through the creator-owned session: a background coordinator
    # has no request identity to check a subject against (unlike
    # authorize_owned_row), so — mirroring preparation_finalizer.py's own
    # precedent of checking status, never subject — this confirms the
    # session row still exists AND is in the one live status a session can
    # be in once its Candidate jobs exist ('frozen_inputs' — nothing in
    # this codebase ever moves a session away from that status once Revision
    # 1 is frozen). Unreachable in V1 (sessions are never deleted or reset
    # post-freeze) but defended anyway, same "unreachable but defended"
    # shape preparation_finalizer.py already established for its own
    # zero-ready-documents guard.
    sessions_table = AnalysisSession.__table__
    session_row = (
        db.execute(select(sessions_table).where(sessions_table.c.id == candidate["analysis_session_id"]).with_for_update())
        .mappings()
        .one_or_none()
    )
    if session_row is None:
        db.rollback()
        _unstick_job(db, candidate_job_id, claimed_status, "orphaned analysis_session_id — no matching row")
        return True
    if session_row["status"] != "frozen_inputs":
        db.rollback()
        _unstick_job(
            db, candidate_job_id, claimed_status, f"analysis_session status={session_row['status']!r} not live"
        )
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
    if membership_row is None:
        db.rollback()
        _unstick_job(db, candidate_job_id, claimed_status, "no matching revision_memberships row")
        return True
    if membership_row["outcome"] != "queued":
        # Idempotency backstop (AC#3): already finalized by a prior run (or,
        # in principle, terminalized out of band). Still unstick the job —
        # leaving candidate_jobs.status untouched here would make this row
        # the permanent head of the claim queue, identical to the orphaned-
        # row cases above, even though there is nothing left to do.
        db.rollback()
        _unstick_job(
            db, candidate_job_id, claimed_status, f"revision_membership already advanced to {membership_row['outcome']!r}"
        )
        return True

    now = datetime.now(timezone.utc)
    result_fields: dict = {
        "overall_score_bps_numerator": None,
        "overall_score_bps_denominator": None,
        "mandatory_skills_score_numerator": None,
        "mandatory_skills_score_denominator": None,
        "relevant_experience_score_numerator": None,
        "relevant_experience_score_denominator": None,
        "coverage_bps_numerator": None,
        "coverage_bps_denominator": None,
        "precise_score_percent": None,
        "headline_whole_percent": None,
        "component_contribution_display": None,
        "gate_codes": None,
        "failure_category": None,
        "failure_correlation_reference": None,
    }

    if claimed_status == "completed":
        inputs = _assemble_scoreability_inputs(db, job, candidate)
        scoreability = evaluate_candidate_scoreability(**inputs)
        if scoreability.needs_review:
            outcome = "NeedsReview"
            result_fields["gate_codes"] = list(scoreability.gate_codes)
            num, denom = _fraction_columns(scoreability.coverage_bps)
            result_fields["coverage_bps_numerator"] = num
            result_fields["coverage_bps_denominator"] = denom
        else:
            outcome = "NewResult"
            score = scoreability.score
            num, denom = _fraction_columns(score.overall_score_bps)
            result_fields["overall_score_bps_numerator"] = num
            result_fields["overall_score_bps_denominator"] = denom
            num, denom = _fraction_columns(score.mandatory_skills_score)
            result_fields["mandatory_skills_score_numerator"] = num
            result_fields["mandatory_skills_score_denominator"] = denom
            num, denom = _fraction_columns(score.relevant_experience_score)
            result_fields["relevant_experience_score_numerator"] = num
            result_fields["relevant_experience_score_denominator"] = denom
            num, denom = _fraction_columns(scoreability.coverage_bps)
            result_fields["coverage_bps_numerator"] = num
            result_fields["coverage_bps_denominator"] = denom
            result_fields["precise_score_percent"] = scoreability.precise_score_percent
            result_fields["headline_whole_percent"] = scoreability.headline_whole_percent
            # Stored as {component_value: str(Decimal)} (e.g. "12.34") —
            # str(Decimal) round-trips exactly via Decimal(the_string), the
            # same reconstruction contract as the numerator/denominator
            # pairs above, never a raw JSON number (JSON has no exact
            # decimal type).
            result_fields["component_contribution_display"] = {
                component.value: str(value) for component, value in scoreability.component_contribution_display.items()
            }
    else:
        outcome = "Failed"
        result_fields["failure_category"] = map_failure_reason_to_category(job["failure_reason"])
        result_fields["failure_correlation_reference"] = candidate_job_id

    db.add(
        CandidateResult(
            id=str(uuid.uuid4()),
            analysis_revision_id=analysis_revision_id,
            candidate_id=candidate_id,
            candidate_job_id=candidate_job_id,
            outcome=outcome,
            created_at=now,
            **result_fields,
        )
    )

    membership_cas = db.execute(
        memberships_table.update()
        .where(memberships_table.c.analysis_revision_id == analysis_revision_id)
        .where(memberships_table.c.candidate_id == candidate_id)
        .where(memberships_table.c.outcome == "queued")
        .values(outcome=outcome)
    )
    if membership_cas.rowcount != 1:
        db.rollback()
        return True

    job_cas = db.execute(
        jobs_table.update()
        .where(jobs_table.c.id == candidate_job_id)
        .where(jobs_table.c.status == claimed_status)
        .values(status="finalized")
    )
    if job_cas.rowcount != 1:
        db.rollback()
        return True

    # Default-insert Shortlist on every outcome branch (not success-only):
    # AC#1's "preserves Candidate-owned Shortlist" must hold even when a
    # Candidate's first outcome is Needs Review or Failed. ON CONFLICT DO
    # NOTHING is the load-bearing half of "without overwriting an existing
    # Candidate-owned Shortlist" — an explicit human choice already on file
    # is never touched.
    shortlists_table = Shortlist.__table__
    db.execute(
        pg_insert(shortlists_table)
        .values(
            id=str(uuid.uuid4()),
            candidate_id=candidate_id,
            state="NotShortlisted",
            version=1,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["candidate_id"])
    )

    revisions_table = AnalysisRevision.__table__
    db.execute(
        revisions_table.update()
        .where(revisions_table.c.id == analysis_revision_id)
        .values(requested_version=revisions_table.c.requested_version + 1)
    )

    db.commit()
    return True
