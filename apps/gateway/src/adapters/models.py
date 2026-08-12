"""SQLAlchemy models backing the local identity adapter (AD-21).

Single-Organization V1 (CLAUDE.md): admission is a per-user approval flag,
not a membership table — there is only ever one private Organization.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
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
    preparation job" AC#1 requires — full lease/fencing columns (AD-6)
    arrive with Story 4.1, once a coordinator actually claims this row.
    `status` reserves MODELS.md's full sub-state vocabulary; this story only
    ever writes `"queued"`.
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
