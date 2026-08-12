"""New Analysis Job Description draft boundary (Story 3.1, FR-3, AR-8, AF-2).

Reuses `require_admitted_identity`, `authorize_owned_row`, and
`_latest_owned_session_row` as-is — this module adds one idempotent
get-or-create route and one version-checked save route on top of the
existing `analysis_sessions` table (Epic 3 extends the Epic-2 table per
CLAUDE.md, it does not replace it).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..domain.document_intake import MAX_DOCUMENT_COUNT, MAX_IDEMPOTENCY_KEY_LENGTH
from ..domain.identity import Identity
from .authorization import authorize_owned_row
from .db import get_db
from .identity import require_admitted_identity
from .models import AnalysisSession, Document, StartPreparation
from .workspace import _latest_owned_session_row

router = APIRouter(prefix="/new-analysis", tags=["new-analysis"])

# Same neutral shape Story 1.5c/2.3 established for every missing/tampered/
# cross-owner object — not a new convention.
_NOT_FOUND = HTTPException(status_code=404, detail="Not found")

# id is String(36) (models.py); anything longer can never match a row, but
# would otherwise reach Postgres and raise a truncation error there instead
# of falling through to the neutral 404 an oversized/tampered id must get.
_MAX_ID_LENGTH = 36

# AF-2 (TRACEABILITY.md: "Job Description validation | >=200 non-whitespace
# characters and frozen validation rules") — the frozen content fixture IS
# this character-count rule; no separate coherence heuristic exists for the
# Job Description (AR-26's coherent-block rule is Resume-scoreability-only).
MINIMUM_NON_WHITESPACE_CHARACTERS = 200

# Pragmatic request-size guard at a trust boundary (NFR-9: content is
# untrusted). Not a PRD product rule — storage/payload hygiene only.
_MAX_JOB_DESCRIPTION_LENGTH = 20_000

_WHITESPACE = re.compile(r"\s")


class SaveJobDescriptionRequest(BaseModel):
    job_description_text: str = Field(max_length=_MAX_JOB_DESCRIPTION_LENGTH)
    expected_version: int = Field(ge=0)


def _validate_job_description(text: str) -> dict[str, Any]:
    non_whitespace_count = len(_WHITESPACE.sub("", text))
    return {
        "non_whitespace_count": non_whitespace_count,
        "is_valid": non_whitespace_count >= MINIMUM_NON_WHITESPACE_CHARACTERS,
        "minimum_required": MINIMUM_NON_WHITESPACE_CHARACTERS,
    }


def _project_preparation(row: Mapping[str, Any]) -> dict[str, Any]:
    """Only these fields ever leave this module (NFR-12) — `document_versions`,
    `idempotency_key`, `request_fingerprint` are internal-only."""
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


def _full_project(db: OrmSession, row: Mapping[str, Any]) -> dict[str, Any]:
    """The complete draft/locked-session projection — deliberately separate
    from `workspace.py`'s minimal `_project` (id/status/created_at only),
    which stays untouched by this story. `preparation` is `null` for a
    `draft` session and also `null` if a non-`draft` session has no
    `StartPreparation` row yet (e.g. a directly-seeded test fixture) —
    otherwise the locked session's Start Preparation snapshot (AC#3:
    reconstructing locked state on refresh)."""
    preparation = None
    if row["status"] != "draft":
        prep_table = StartPreparation.__table__
        prep_row = (
            db.execute(select(prep_table).where(prep_table.c.analysis_session_id == row["id"]))
            .mappings()
            .one_or_none()
        )
        if prep_row is not None:
            preparation = _project_preparation(prep_row)

    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "job_description_text": row["job_description_text"],
        "job_description_version": row["job_description_version"],
        "validation": _validate_job_description(row["job_description_text"]),
        "preparation": preparation,
    }


@router.post("")
def get_or_create_draft(
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> Any:
    """Idempotent get-or-create: returns the creator's existing draft, or
    creates a fresh empty one if none exists. Sequential repeat calls never
    create a second draft (AC#3's anti-duplication spirit) — this is a
    plain check-then-insert with no unique constraint or lock, so two
    truly concurrent first-ever calls for the same creator have a narrow
    race window that could insert two draft rows. Accepted V1 shortcut
    (AR-45, single-Postgres-process demo scope) — see deferred-work.md.

    Story 3.4 (AC#3): a non-`draft` session is never a reason to create a
    second one (unchanged — "prevents a second session" was always carried
    by this branch, not by any particular status code) — it now returns
    `200` with the full projection (including the nested `preparation`
    snapshot) instead of `409`, so a browser refresh while a session is
    locked reconstructs that locked state instead of dead-ending."""
    row = _latest_owned_session_row(db, identity)

    if row is not None and row["status"] != "draft":
        return JSONResponse(status_code=200, content=_full_project(db, row))

    if row is None:
        new_id = str(uuid.uuid4())
        db.add(
            AnalysisSession(
                id=new_id,
                creator_issuer=identity.issuer,
                creator_subject=identity.subject,
                status="draft",
                created_at=datetime.now(timezone.utc),
                job_description_text="",
                job_description_version=0,
            )
        )
        db.commit()
        row = _latest_owned_session_row(db, identity)
        return JSONResponse(status_code=201, content=_full_project(db, row))

    return JSONResponse(status_code=200, content=_full_project(db, row))


@router.put("/{session_id}")
def save_job_description(
    session_id: str,
    body: SaveJobDescriptionRequest,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> Any:
    if len(session_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND

    table = AnalysisSession.__table__
    row = authorize_owned_row(
        db, identity, table, table.c.id, table.c.creator_subject, session_id
    )
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    if row["status"] != "draft":
        raise HTTPException(status_code=409, detail="This draft is no longer editable")

    if row["job_description_version"] != body.expected_version:
        # AC#3: stale version cannot overwrite a newer draft — return the
        # current projection so the client can recover without a second
        # round trip.
        return JSONResponse(status_code=409, content=_full_project(db, row))

    # The version check above is a compare-and-swap, not just a read: the
    # UPDATE's WHERE clause re-checks `job_description_version` at the
    # database level so two concurrent requests that both read the same
    # stale version cannot both succeed — only the first commits, and its
    # own `job_description_version + 1` makes the second's WHERE clause
    # match zero rows.
    result = db.execute(
        table.update()
        .where(table.c.id == session_id)
        .where(table.c.job_description_version == body.expected_version)
        .where(table.c.status == "draft")
        .values(
            job_description_text=body.job_description_text,
            job_description_version=table.c.job_description_version + 1,
        )
    )
    if result.rowcount == 0:
        db.rollback()
        current = db.execute(select(table).where(table.c.id == session_id)).mappings().one()
        return JSONResponse(status_code=409, content=_full_project(db, current))

    db.commit()
    updated = db.execute(select(table).where(table.c.id == session_id)).mappings().one()
    return JSONResponse(status_code=200, content=_full_project(db, updated))


class AnalyzeRequest(BaseModel):
    expected_job_description_version: int = Field(ge=0)
    # Capped at MAX_DOCUMENT_COUNT (review finding): unlike every other
    # capacity-sensitive input in this codebase, this dict had no bound —
    # a caller could submit an arbitrarily large object and force
    # unbounded iteration/hashing before any rejection.
    expected_document_versions: dict[str, int] = Field(max_length=MAX_DOCUMENT_COUNT)
    idempotency_key: str = Field(max_length=MAX_IDEMPOTENCY_KEY_LENGTH)


def _compute_request_fingerprint(
    expected_job_description_version: int, expected_document_versions: dict[str, int]
) -> str:
    """AD-16's "request fingerprint" — a hash of what the client asked to
    lock, used to distinguish a safe idempotency-key replay from a caller
    reusing a stale key for a different request. Not a content hash of the
    Job Description/Document bytes themselves — see story Dev Notes for why
    the existing version integers already serve as that guard."""
    canonical = json.dumps(
        {
            "job_description_version": expected_job_description_version,
            "document_versions": sorted(expected_document_versions.items()),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post("/{session_id}/analyze")
def analyze(
    session_id: str,
    body: AnalyzeRequest,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> Any:
    """Story 3.4 (FR-4, FR-7, AR-9, AR-10): idempotently creates or returns
    the session's one Start Preparation, locking the Job Description and
    Documents. Deliberately does not reuse `_authorize_draft_session`'s
    unconditional-409-on-non-draft shape (document_upload.py) — this route
    must distinguish "no preparation yet" from "preparation already exists"
    for a non-draft session, not treat every non-draft session as a bare
    conflict."""
    if len(session_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND

    table = AnalysisSession.__table__
    row = authorize_owned_row(
        db, identity, table, table.c.id, table.c.creator_subject, session_id
    )
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    fingerprint = _compute_request_fingerprint(
        body.expected_job_description_version, body.expected_document_versions
    )

    prep_table = StartPreparation.__table__
    existing_prep = (
        db.execute(select(prep_table).where(prep_table.c.analysis_session_id == session_id))
        .mappings()
        .one_or_none()
    )

    if existing_prep is not None:
        same_key = existing_prep["idempotency_key"] == body.idempotency_key
        same_fingerprint = existing_prep["request_fingerprint"] == fingerprint
        if same_fingerprint:
            # AC#1: a true replay (same key) and a duplicate/double-click
            # command (different key, identical target state) both return
            # the one existing preparation.
            return JSONResponse(status_code=202, content=_project_preparation(existing_prep))
        if same_key:
            # AD-16: a changed request reusing a stale key is a caller bug,
            # not a safe replay.
            return JSONResponse(status_code=409, content={"error": "idempotency_key_conflict"})
        # Different key, different fingerprint: a different Analyze attempt
        # while one is already active (UX-DR7's active-boundary conflict).
        return JSONResponse(
            status_code=409,
            content={
                "error": "active_preparation_exists",
                "preparation": _project_preparation(existing_prep),
            },
        )

    if row["status"] != "draft":
        # No StartPreparation row exists yet, but the session already left
        # draft — unreachable given start_preparations' unique constraint on
        # analysis_session_id (every status transition inserts one in the
        # same transaction); defensive fallback only.
        return JSONResponse(status_code=409, content={"error": "active_preparation_exists"})

    errors: list[dict[str, str]] = []

    validation = _validate_job_description(row["job_description_text"])
    if not validation["is_valid"]:
        errors.append({"field": "job_description", "reason": "below_minimum_length"})
    if row["job_description_version"] != body.expected_job_description_version:
        errors.append({"field": "job_description", "reason": "stale_version"})

    # Locking read (Story 3.4 Dev Notes): holds a row lock on every Ready
    # Document for the rest of this transaction, so a concurrent
    # remove_document/replace_document targeting the same row blocks until
    # this transaction commits or rolls back — closing the window between
    # this validation read and the session CAS below.
    documents_table = Document.__table__
    ready_documents = (
        db.execute(
            select(documents_table)
            .where(documents_table.c.analysis_session_id == session_id)
            .where(documents_table.c.status == "ready")
            # Deterministic lock order (review finding): without an
            # explicit ORDER BY, two concurrent Analyze calls locking the
            # same session's Document rows could acquire them in different
            # orders and deadlock. Ordering by id makes every caller
            # acquire locks in the same sequence.
            .order_by(documents_table.c.id)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    if len(ready_documents) == 0:
        errors.append({"field": "documents", "reason": "none_ready"})

    ready_by_id = {d["id"]: d["content_version"] for d in ready_documents}
    for document_id, version in body.expected_document_versions.items():
        if document_id not in ready_by_id:
            errors.append({"field": f"document:{document_id}", "reason": "unexpected"})
        elif ready_by_id[document_id] != version:
            errors.append({"field": f"document:{document_id}", "reason": "stale_version"})
    for document_id in ready_by_id:
        if document_id not in body.expected_document_versions:
            errors.append({"field": f"document:{document_id}", "reason": "missing"})

    if errors:
        # AC#2: no write of any kind happens on a validation failure — the
        # session stays `draft`, every input stays exactly as it was.
        db.rollback()
        return JSONResponse(status_code=409, content={"errors": errors})

    # Atomic CAS lock — one statement, not read-then-write. Re-checks
    # job_description_version here too (review finding): the Ready-Document
    # snapshot above is protected by its own FOR UPDATE lock, but nothing
    # protected the Job Description version between that validation read and
    # this UPDATE until now — a concurrent save_job_description could
    # otherwise bump the version after validation passed but before this CAS
    # committed, locking an inconsistent snapshot. Only the caller whose
    # UPDATE matches ever reaches the INSERT below.
    result = db.execute(
        table.update()
        .where(table.c.id == session_id)
        .where(table.c.status == "draft")
        .where(table.c.job_description_version == body.expected_job_description_version)
        .values(status="preparing_to_start")
    )
    if result.rowcount == 0:
        db.rollback()
        current = db.execute(select(table).where(table.c.id == session_id)).mappings().one()
        if current["status"] == "draft":
            # Still draft: the CAS lost to a concurrent Job Description save
            # that changed the version, not to another Analyze call — a
            # genuine stale-version validation failure, not a lock conflict.
            return JSONResponse(
                status_code=409,
                content={"errors": [{"field": "job_description", "reason": "stale_version"}]},
            )
        # Lost a race to a concurrent Analyze call for this session.
        winner = (
            db.execute(select(prep_table).where(prep_table.c.analysis_session_id == session_id))
            .mappings()
            .one_or_none()
        )
        if winner is not None and winner["request_fingerprint"] == fingerprint:
            return JSONResponse(status_code=202, content=_project_preparation(winner))
        # Same response shape as the pre-check active-boundary branch above
        # (review finding: these were previously inconsistent) — always
        # nests `preparation` when one is known, `null` otherwise.
        return JSONResponse(
            status_code=409,
            content={
                "error": "active_preparation_exists",
                "preparation": _project_preparation(winner) if winner is not None else None,
            },
        )

    new_id = str(uuid.uuid4())
    db.execute(
        prep_table.insert().values(
            id=new_id,
            analysis_session_id=session_id,
            status="queued",
            job_description_version=body.expected_job_description_version,
            document_versions=body.expected_document_versions,
            idempotency_key=body.idempotency_key,
            request_fingerprint=fingerprint,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    created = db.execute(select(prep_table).where(prep_table.c.id == new_id)).mappings().one()
    return JSONResponse(status_code=202, content=_project_preparation(created))
