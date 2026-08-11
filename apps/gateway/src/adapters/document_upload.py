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

    # 3. Extension check.
    if not check_extension(filename):
        return _rejected(filename, RejectionCategory.EXTENSION_REJECTED)

    # 4. Bounded-memory streamed size check.
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
        return _rejected(filename, RejectionCategory.SIZE_LIMIT)
    data_bytes = bytes(data)

    # 5. Signature check — must also match the declared extension.
    detected_type = detect_signature(data_bytes[:8])
    extension_type = PDF_CONTENT_TYPE if filename.lower().endswith(".pdf") else DOCX_CONTENT_TYPE
    if detected_type is None or detected_type != extension_type:
        return _rejected(filename, RejectionCategory.SIGNATURE_MISMATCH)

    # 6. Password/encryption + container integrity.
    if detected_type == PDF_CONTENT_TYPE:
        container_result = check_pdf_password_and_container(data_bytes)
    else:
        container_result = check_docx_password_and_container(data_bytes)
    if container_result == "password_protected":
        return _rejected(filename, RejectionCategory.PASSWORD_PROTECTED)
    if container_result == "corrupt_container":
        return _rejected(filename, RejectionCategory.CORRUPT_CONTAINER)

    # 7. Archive-expansion (DOCX only).
    if detected_type == DOCX_CONTENT_TYPE and not check_docx_archive_expansion(data_bytes):
        return _rejected(filename, RejectionCategory.ARCHIVE_EXPANSION)

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


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _remove_orphaned_file(storage_path: str) -> None:
    """Best-effort cleanup for bytes written just before an insert failed —
    a missing file here is not itself an error worth surfacing."""
    try:
        os.remove(storage_path)
    except OSError:
        pass
