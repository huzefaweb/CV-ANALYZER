"""SQLAlchemy models backing the local identity adapter (AD-21).

Single-Organization V1 (CLAUDE.md): admission is a per-user approval flag,
not a membership table — there is only ever one private Organization.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
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
