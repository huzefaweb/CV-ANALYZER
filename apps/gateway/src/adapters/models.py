"""SQLAlchemy models backing the local identity adapter (AD-21).

Single-Organization V1 (CLAUDE.md): admission is a per-user approval flag,
not a membership table — there is only ever one private Organization.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisSession(Base):
    """Minimal Epic-2 shape (AD-3) plus Story 3.1's Job Description draft
    fields — Epic 3+ extends this same table, it does not replace it.
    """

    __tablename__ = "analysis_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    creator_issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    creator_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_description_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    job_description_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Document(Base):
    """Story 3.2 (AR-8, AR-36): one accepted Resume Document per row.

    Rejected intake never reaches this table (AF-3) — only validated,
    stored files are persisted here. `storage_path` and `idempotency_key`
    are internal-only and must never leave the module that reads this row
    (NFR-12).
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_reference: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    storage_path: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Story 3.3: records the idempotency key of the most recently applied
    # remove/replace command on this row — a replay marker read back against
    # the row's own current state, not an insert-race collision backstop
    # (that's what idempotency_key/its unique index are for).
    last_command_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class StartPreparation(Base):
    """Story 3.4 (AR-9, AR-10): the persisted lock created by `Analyze`.

    One row serves as both "the Start Preparation" and "the one logical
    preparation job" AC#1 requires. `status` reserves MODELS.md's full
    sub-state vocabulary; Story 3.4 only wrote `"queued"`. Story 3.5 extends
    the vocabulary this row's `status` actually cycles through: `queued ->
    deriving -> validated -> frozen` (success) or `queued -> deriving ->
    queued` (attempt 2) `-> deriving -> failed` (exhausted). Story 4.1 adds
    full AD-6 lease/fencing (`generation`/`lease_token`/`lease_expires_at`/
    `state_version`/`reclaim_count`) on top of that same status cycle —
    every worker mutation now also fences on generation+token, not status
    alone.
    """

    __tablename__ = "start_preparations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_session_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    job_description_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document_versions: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Story 3.5:
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    proposal_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Story 4.1 (AD-6): lease/fencing.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reclaim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class JobRequirement(Base):
    """Story 3.5 (AR-10, AD-4): one canonical, frozen Job Requirement."""

    __tablename__ = "job_requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    display_id: Mapped[str] = mapped_column(String(8), nullable=False)
    component: Mapped[str] = mapped_column(String(32), nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_locators: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScoringConfiguration(Base):
    """Story 3.5 (AF-6, AD-4): one row per rubric Component, frozen with Revision 1."""

    __tablename__ = "scoring_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    component: Mapped[str] = mapped_column(String(32), nullable=False)
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_weight_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Candidate(Base):
    """Story 3.5 (AD-5): one accepted Document slot, stable within its Analysis Session."""

    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    document_reference: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisRevision(Base):
    """Story 3.5 (AD-5): immutable cohort/publication boundary. This story only ever
    creates `revision_number=1`; retry-created Revision 2+ is Epic 4+'s scope."""

    __tablename__ = "analysis_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="frozen")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Story 4.6 (AR-19): bumped once per Candidate finalization commit on this
    # revision; Story 5.1's publication coordinator is the future reader.
    requested_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Story 5.1 (AD-7, AR-20): the last requested_version successfully
    # published; published_at IS NOT NULL is the sole "has this revision ever
    # published" signal (AnalysisRevision.status is not repurposed for this —
    # it is set once to "frozen" by Story 3.5 and read nowhere else).
    published_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ranked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    needs_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RevisionMembership(Base):
    """Story 3.5 (AD-5): exactly one row per (revision, Candidate). `outcome` reserves
    the full ReusedResult/NewResult/NeedsReview/Failed vocabulary; this story only
    ever writes `"queued"` — Epic 4 advances it."""

    __tablename__ = "revision_memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Story 5.1 (AD-10, AR-30): populated only for ranked (NewResult/
    # ReusedResult) members of a published revision; NULL for NeedsReview/
    # Failed/unpublished ("unranked outcomes have null rank" — AR-13).
    rank_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tie_group: Mapped[int | None] = mapped_column(Integer, nullable=True)
    presentation_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CandidateJob(Base):
    """Story 3.5 (AR-11): one required Candidate job per membership. Story
    4.1 (AD-6) adds full lease/fencing (`generation`/`lease_token`/
    `lease_expires_at`/`state_version`/`reclaim_count`) plus `attempt`/
    `failure_reason`, mirroring `StartPreparation`'s columns, and widens
    `status` to also carry `"claimed"` (worker holds an active lease). A
    lease-exhausted job here (`status="failed"`) is this story's AD-6
    job-lease terminal state — a distinct, earlier-layer concept from
    `revision_memberships.outcome="Failed"`, the authoritative membership
    outcome only Story 4.6's Candidate finalizer ever commits. Claim
    mechanics exist and are tested against this table (`candidate_claim.py`)
    but nothing in production calls them yet — no real parse/provider work
    exists to run until Story 4.2+."""

    __tablename__ = "candidate_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Story 4.1 (AD-6): lease/fencing + attempt budget.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reclaim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ParseArtifact(Base):
    """Story 4.2 (AD-8, AR-21): one immutable parse artifact per (Document,
    content version, parser/pipeline version) — append-only, never
    overwritten (enforced by a unique index on that triple, not application
    logic alone). `source_units_json` is AR-21's secured locator map
    (`ResumeSourceUnit`/`Locator` shape verbatim); `blocks_json` is the
    non-AI content-class grouping (`parse_gates.classify_blocks`); neither
    ever carries identity/contact content (that lives in
    `CandidateIdentity`, joined only for authorized display, AD-9)."""

    __tablename__ = "parse_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_content_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_pipeline_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source_units_json: Mapped[list] = mapped_column(JSON, nullable=False)
    blocks_json: Mapped[list] = mapped_column(JSON, nullable=False)
    gate_codes: Mapped[list] = mapped_column(JSON, nullable=False)
    coherent_block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CandidateIdentity(Base):
    """Story 4.2 (AD-9): one display-only identity/contact record per
    Candidate, upserted on re-parse. Joined only for authorized display
    (creator-through-session re-check at read time, AR-8) — never read into
    any provider/scoring/ranking input path. `name_source` distinguishes a
    genuinely parsed name from the Document Reference/filename fallback
    (UX-DR10)."""

    __tablename__ = "candidate_identities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name_source: Mapped[str] = mapped_column(String(16), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CandidateProposal(Base):
    """Story 4.4 (AD-8, AR-24, AR-40): one immutable provider proposal per
    Candidate job, staged only after `validate_complete`/`validate_locators`
    pass (or, on token-budget overflow, an empty `items_json` with
    `gate_codes=["COVERAGE_BELOW_7000_BPS"]` and no provider call at all —
    Story 4.3's `check_budget`, finally consumed here). Same
    exactly-once-per-job shape as `ParseArtifact`: a unique index on
    `candidate_job_id` makes a duplicate fenced write for the same job a
    no-op, never a second row. The worker only ever inserts here — scoring
    (Story 4.5) and terminalization (Story 4.6) are what read this table,
    never this module."""

    __tablename__ = "candidate_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    analysis_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    items_json: Mapped[list] = mapped_column(JSON, nullable=False)
    gate_codes: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CandidateResult(Base):
    """Story 4.6 (AR-12, AR-19, AR-27): one immutable, append-only Candidate
    Result Version per finalized `(analysis_revision_id, candidate_id)` —
    enforced by a unique index, not application logic alone. `outcome` is
    one of `"NewResult"`/`"NeedsReview"`/`"Failed"` (this story never writes
    `"ReusedResult"` — that is Story 5.3's retry-revision reuse). Score
    fields are populated only when `outcome == "NewResult"`; `gate_codes`
    only when `"NeedsReview"`; `failure_category`/`failure_correlation_reference`
    only when `"Failed"`. Exact-rational fields (AR-27) are persisted as
    reduced numerator/positive-denominator `NUMERIC` pairs, never a single
    float/Decimal column — reconstruct via `Fraction(int(num), int(denom))`.
    """

    __tablename__ = "candidate_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    overall_score_bps_numerator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    overall_score_bps_denominator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    mandatory_skills_score_numerator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    mandatory_skills_score_denominator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    relevant_experience_score_numerator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    relevant_experience_score_denominator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    coverage_bps_numerator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    coverage_bps_denominator: Mapped[object | None] = mapped_column(Numeric(), nullable=True)
    precise_score_percent: Mapped[object | None] = mapped_column(Numeric(5, 2), nullable=True)
    headline_whole_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    component_contribution_display: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gate_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_correlation_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Shortlist(Base):
    """Story 4.6/AR-33: one Candidate-owned Shortlist state, stable across
    revisions (never per-membership). This story only ever default-inserts
    at `state="NotShortlisted"`, `version=1` on first finalization of a
    Candidate — Story 6.x owns the versioned PUT/DELETE mutation."""

    __tablename__ = "shortlists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="NotShortlisted")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
