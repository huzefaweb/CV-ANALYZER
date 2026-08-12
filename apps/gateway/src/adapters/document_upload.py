"""Document intake: streamed, independently validated Resume upload (FR-5,
FR-6, NFR-8, AR-8, AR-36). One file per request — see story Dev Notes for
why not a multipart-batch endpoint: each call independently re-checks
capacity, which is what makes "accept in picker/drop order, reject only
the remainder" work correctly, and each file naturally gets its own
idempotency key/row (AC#3).
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..domain.document_intake import (
    ALLOWED_EXTENSIONS,
    DOCX_CONTENT_TYPE,
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_COUNT,
    MAX_FILENAME_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    PDF_CONTENT_TYPE,
    RejectionCategory,
    check_extension,
    detect_signature,
)
from .authorization import authorize_owned_row
from .db import get_db
from .document_detection import (
    check_docx_archive_expansion,
    check_docx_password_and_container,
    check_pdf_password_and_container,
)
from .document_storage import store
from .identity import require_admitted_identity
from ..domain.identity import Identity
from .models import AnalysisSession, Document

router = APIRouter(prefix="/new-analysis", tags=["new-analysis"])

# Same neutral shape workspace.py/new_analysis.py already established.
_NOT_FOUND = HTTPException(status_code=404, detail="Not found")
_MAX_ID_LENGTH = 36

# Bounded-memory streamed read chunk size (see story Dev Notes: this bounds
# this story's own application-level buffering to the 10 MB business cap,
# not a raw-ASGI-level streaming guarantee).
_CHUNK_SIZE = 65536
_REFERENCE_GENERATION_ATTEMPTS = 5


def _project(row: Mapping[str, Any]) -> dict[str, Any]:
    """Only these fields ever leave this module (NFR-12) — storage_path and
    idempotency_key are internal-only, never sent to the client (AR-36)."""
    return {
        "id": row["id"],
        "document_reference": row["document_reference"],
        "original_filename": row["original_filename"],
        "content_version": row["content_version"],
        "size_bytes": row["size_bytes"],
        "content_type": row["content_type"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


def _rejected(filename: str, category: RejectionCategory) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"filename": filename, "rejected": True, "category": category.value},
    )


def _authorize_draft_session(db: OrmSession, identity: Identity, session_id: str) -> Mapping[str, Any]:
    if len(session_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND
    table = AnalysisSession.__table__
    row = authorize_owned_row(db, identity, table, table.c.id, table.c.creator_subject, session_id)
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND
    if row["status"] != "draft":
        raise HTTPException(status_code=409, detail="This draft is no longer editable")
    return row


def _authorize_draft_document(
    db: OrmSession, identity: Identity, session_id: str, document_id: str
) -> Mapping[str, Any]:
    """Remove/replace share this: the session-ownership/lock check (AR-8,
    "no mutation after lock") plus a session-scoped Document lookup — a
    cross-session document_id gets the same neutral 404 a cross-owner
    session_id already gets, never a distinguishable error."""
    _authorize_draft_session(db, identity, session_id)
    if len(document_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND
    documents_table = Document.__table__
    row = (
        db.execute(
            select(documents_table)
            .where(documents_table.c.id == document_id)
            .where(documents_table.c.analysis_session_id == session_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise _NOT_FOUND
    return row


async def _validate_and_read(file: UploadFile, filename: str) -> tuple[bytes, str] | RejectionCategory:
    """Shared by upload and replace (Story 3.3): extension, streamed
    bounded-memory size, signature, password/container, archive-expansion —
    exactly the same deterministic precedence and detection this module
    already established (Story 3.2). Excludes the count check, which is
    upload-only (a replacement never changes how many Documents exist)."""
    if not check_extension(filename):
        await file.close()
        return RejectionCategory.EXTENSION_REJECTED

    data = bytearray()
    oversized = False
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_DOCUMENT_BYTES:
            oversized = True
            break
    await file.close()
    if oversized:
        return RejectionCategory.SIZE_LIMIT
    data_bytes = bytes(data)

    detected_type = detect_signature(data_bytes[:8])
    extension_type = PDF_CONTENT_TYPE if filename.lower().endswith(".pdf") else DOCX_CONTENT_TYPE
    if detected_type is None or detected_type != extension_type:
        return RejectionCategory.SIGNATURE_MISMATCH

    if detected_type == PDF_CONTENT_TYPE:
        container_result = check_pdf_password_and_container(data_bytes)
    else:
        container_result = check_docx_password_and_container(data_bytes)
    if container_result == "password_protected":
        return RejectionCategory.PASSWORD_PROTECTED
    if container_result == "corrupt_container":
        return RejectionCategory.CORRUPT_CONTAINER

    if detected_type == DOCX_CONTENT_TYPE and not check_docx_archive_expansion(data_bytes):
        return RejectionCategory.ARCHIVE_EXPANSION

    return data_bytes, detected_type


class RemoveDocumentRequest(BaseModel):
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(max_length=MAX_IDEMPOTENCY_KEY_LENGTH)


@router.get("/{session_id}/documents")
def list_documents(
    session_id: str,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict[str, list[dict[str, Any]]]:
    if len(session_id) > _MAX_ID_LENGTH:
        raise _NOT_FOUND
    session_table = AnalysisSession.__table__
    row = authorize_owned_row(
        db, identity, session_table, session_table.c.id, session_table.c.creator_subject, session_id
    )
    if row is None or row["creator_issuer"] != identity.issuer:
        raise _NOT_FOUND

    documents_table = Document.__table__
    rows = (
        db.execute(
            select(documents_table)
            .where(documents_table.c.analysis_session_id == session_id)
            .where(documents_table.c.status == "ready")
            .order_by(documents_table.c.created_at.asc(), documents_table.c.id.asc())
        )
        .mappings()
        .all()
    )
    return {"documents": [_project(row) for row in rows]}


@router.post("/{session_id}/documents")
async def upload_document(
    session_id: str,
    file: UploadFile,
    idempotency_key: str = Form(...),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
):
    _authorize_draft_session(db, identity, session_id)

    filename = file.filename or ""

    # 0. Request-shape guard — an oversized filename/idempotency_key would
    # otherwise reach the INSERT and raise an uncaught DataError (Postgres
    # enforces VARCHAR length at write time, not on a WHERE comparison), so
    # this is checked first, before any DB work.
    if len(filename) > MAX_FILENAME_LENGTH or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        return _rejected(filename, RejectionCategory.INVALID_REQUEST)

    documents_table = Document.__table__

    # 1. Idempotency replay check (AC#3) — before any validation work.
    existing = (
        db.execute(
            select(documents_table)
            .where(documents_table.c.analysis_session_id == session_id)
            .where(documents_table.c.idempotency_key == idempotency_key)
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        return JSONResponse(status_code=200, content=_project(existing))

    # 2. Count check.
    current_count = db.execute(
        select(func.count())
        .select_from(documents_table)
        .where(documents_table.c.analysis_session_id == session_id)
        .where(documents_table.c.status == "ready")
    ).scalar_one()
    if current_count >= MAX_DOCUMENT_COUNT:
        return _rejected(filename, RejectionCategory.COUNT_EXCEEDED)

    # 3-7. Extension/size/signature/password/archive-expansion — shared with
    # replace (Task 2's extraction).
    validated = await _validate_and_read(file, filename)
    if isinstance(validated, RejectionCategory):
        return _rejected(filename, validated)
    data_bytes, detected_type = validated

    # 8. Accept: generate a collision-checked Document Reference, store
    # bytes, insert the row.
    document_id = None
    document_reference = None
    for _ in range(_REFERENCE_GENERATION_ATTEMPTS):
        candidate_id = _new_uuid()
        candidate_reference = f"D-{secrets.token_hex(3)}"
        exists = (
            db.execute(
                select(documents_table)
                .where(documents_table.c.analysis_session_id == session_id)
                .where(documents_table.c.document_reference == candidate_reference)
            )
            .mappings()
            .one_or_none()
        )
        if exists is None:
            document_id = candidate_id
            document_reference = candidate_reference
            break
    if document_id is None or document_reference is None:
        return JSONResponse(
            status_code=422,
            content={"filename": filename, "rejected": True, "category": RejectionCategory.CONFLICT.value},
        )

    storage_path = store(document_id, 1, data_bytes)

    try:
        db.execute(
            documents_table.insert().values(
                id=document_id,
                analysis_session_id=session_id,
                document_reference=document_reference,
                original_filename=filename,
                content_version=1,
                storage_path=storage_path,
                size_bytes=len(data_bytes),
                content_type=detected_type,
                status="ready",
                idempotency_key=idempotency_key,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        # The just-written bytes have no owning row now — clean up rather
        # than leak them on the restricted-source volume.
        _remove_orphaned_file(storage_path)
        # A concurrent request with the same idempotency_key may have won
        # the race and committed first — AC#3 requires the replay guarantee
        # to hold even under a true race, not just sequential retries, so
        # re-check before falling back to a generic conflict.
        winner = (
            db.execute(
                select(documents_table)
                .where(documents_table.c.analysis_session_id == session_id)
                .where(documents_table.c.idempotency_key == idempotency_key)
            )
            .mappings()
            .one_or_none()
        )
        if winner is not None:
            return JSONResponse(status_code=200, content=_project(winner))
        return JSONResponse(
            status_code=422,
            content={"filename": filename, "rejected": True, "category": RejectionCategory.CONFLICT.value},
        )

    row = (
        db.execute(select(documents_table).where(documents_table.c.id == document_id))
        .mappings()
        .one()
    )
    return JSONResponse(status_code=201, content=_project(row))


@router.post("/{session_id}/documents/{document_id}/remove")
def remove_document(
    session_id: str,
    document_id: str,
    body: RemoveDocumentRequest,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
):
    row = _authorize_draft_document(db, identity, session_id, document_id)
    documents_table = Document.__table__

    # Idempotent replay (AC#3): the identical remove command already ran —
    # no second UPDATE, return the current (already-removed) projection.
    if row["last_command_idempotency_key"] == body.idempotency_key and row["status"] == "removed":
        return JSONResponse(status_code=200, content=_project(row))

    if row["status"] != "ready":
        # Already removed by a different command, or any other non-ready
        # state — not this command's replay, so this is a real conflict.
        return JSONResponse(status_code=409, content=_project(row))

    # Atomic CAS UPDATE — one statement, not a read-then-write (the exact
    # lesson from Story 3.1's review: the WHERE clause is the concurrency
    # guard, a prior SELECT is not).
    result = db.execute(
        documents_table.update()
        .where(documents_table.c.id == document_id)
        .where(documents_table.c.analysis_session_id == session_id)
        .where(documents_table.c.content_version == body.expected_version)
        .where(documents_table.c.status == "ready")
        .values(status="removed", last_command_idempotency_key=body.idempotency_key)
    )
    if result.rowcount == 0:
        # A 0-row UPDATE changes nothing, but leaves the transaction's read
        # view pinned to the pre-UPDATE snapshot under the default isolation
        # level — roll back so the SELECT just below observes the current
        # committed row (e.g. a concurrent winner's write), not a stale one.
        db.rollback()
        current = (
            db.execute(select(documents_table).where(documents_table.c.id == document_id)).mappings().one()
        )
        if current["last_command_idempotency_key"] == body.idempotency_key and current["status"] == "removed":
            # A concurrent request with the identical key won the race —
            # this is a true-concurrency replay, not a stale conflict.
            return JSONResponse(status_code=200, content=_project(current))
        return JSONResponse(status_code=409, content=_project(current))

    db.commit()
    updated = db.execute(select(documents_table).where(documents_table.c.id == document_id)).mappings().one()
    return JSONResponse(status_code=200, content=_project(updated))


@router.put("/{session_id}/documents/{document_id}")
async def replace_document(
    session_id: str,
    document_id: str,
    file: UploadFile,
    expected_version: int = Form(...),
    idempotency_key: str = Form(...),
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
):
    row = _authorize_draft_document(db, identity, session_id, document_id)
    documents_table = Document.__table__

    filename = file.filename or ""
    if len(filename) > MAX_FILENAME_LENGTH or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        await file.close()
        return _rejected(filename, RejectionCategory.INVALID_REQUEST)
    if expected_version < 0:
        await file.close()
        return _rejected(filename, RejectionCategory.INVALID_REQUEST)

    # Idempotent replay (AC#3): the identical replace command already
    # produced the row's current state — return it without re-reading the
    # uploaded bytes or writing a second content-version file.
    if row["last_command_idempotency_key"] == idempotency_key and row["status"] == "ready":
        await file.close()
        return JSONResponse(status_code=200, content=_project(row))

    if row["status"] != "ready":
        await file.close()
        return JSONResponse(status_code=409, content=_project(row))

    # Validate and read fully before touching the row (AC#2: a failed
    # replacement must leave "the prior valid Document remains current" —
    # nothing is written to the DB or disk until validation passes).
    validated = await _validate_and_read(file, filename)
    if isinstance(validated, RejectionCategory):
        return _rejected(filename, validated)
    data_bytes, detected_type = validated

    new_version = row["content_version"] + 1
    # A per-attempt random nonce (never derived from the filename, AR-36)
    # guarantees two concurrent replace attempts against the same
    # expected_version — which both compute the same new_version — never
    # write to the same path, so whichever one wins the CAS below always
    # references its own, correctly-written bytes.
    storage_path = store(document_id, new_version, data_bytes, nonce=secrets.token_hex(8))

    result = db.execute(
        documents_table.update()
        .where(documents_table.c.id == document_id)
        .where(documents_table.c.analysis_session_id == session_id)
        .where(documents_table.c.content_version == expected_version)
        .where(documents_table.c.status == "ready")
        .values(
            content_version=new_version,
            storage_path=storage_path,
            original_filename=filename,
            size_bytes=len(data_bytes),
            content_type=detected_type,
            last_command_idempotency_key=idempotency_key,
        )
    )
    if result.rowcount == 0:
        # See remove_document's identical comment: rolls back so the
        # follow-up SELECT observes the current committed row, not a
        # snapshot pinned before this failed UPDATE.
        db.rollback()
        current = (
            db.execute(select(documents_table).where(documents_table.c.id == document_id)).mappings().one()
        )
        # Each attempt's storage_path is now unique (the nonce above), so
        # this request's own just-written file can never be the row a
        # concurrent winner committed — always safe to remove.
        _remove_orphaned_file(storage_path)
        if current["last_command_idempotency_key"] == idempotency_key and current["status"] == "ready":
            return JSONResponse(status_code=200, content=_project(current))
        return JSONResponse(status_code=409, content=_project(current))

    db.commit()
    updated = db.execute(select(documents_table).where(documents_table.c.id == document_id)).mappings().one()
    return JSONResponse(status_code=200, content=_project(updated))


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _remove_orphaned_file(storage_path: str) -> None:
    """Best-effort cleanup for bytes written just before an insert failed —
    a missing file here is not itself an error worth surfacing."""
    try:
        os.remove(storage_path)
    except OSError:
        pass
