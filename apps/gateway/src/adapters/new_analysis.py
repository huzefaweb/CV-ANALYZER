"""New Analysis Job Description draft boundary (Story 3.1, FR-3, AR-8, AF-2).

Reuses `require_admitted_identity`, `authorize_owned_row`, and
`_latest_owned_session_row` as-is — this module adds one idempotent
get-or-create route and one version-checked save route on top of the
existing `analysis_sessions` table (Epic 3 extends the Epic-2 table per
CLAUDE.md, it does not replace it).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..domain.identity import Identity
from .authorization import authorize_owned_row
from .db import get_db
from .identity import require_admitted_identity
from .models import AnalysisSession
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


def _full_project(row: Mapping[str, Any]) -> dict[str, Any]:
    """The complete draft projection — deliberately separate from
    `workspace.py`'s minimal `_project` (id/status/created_at only), which
    stays untouched by this story."""
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "job_description_text": row["job_description_text"],
        "job_description_version": row["job_description_version"],
        "validation": _validate_job_description(row["job_description_text"]),
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
    (AR-45, single-Postgres-process demo scope) — see deferred-work.md."""
    row = _latest_owned_session_row(db, identity)

    if row is not None and row["status"] != "draft":
        return JSONResponse(status_code=409, content={"id": row["id"], "status": row["status"]})

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
        return JSONResponse(status_code=201, content=_full_project(row))

    return JSONResponse(status_code=200, content=_full_project(row))


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
        return JSONResponse(status_code=409, content=_full_project(row))

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
        .values(
            job_description_text=body.job_description_text,
            job_description_version=table.c.job_description_version + 1,
        )
    )
    if result.rowcount == 0:
        db.rollback()
        current = db.execute(select(table).where(table.c.id == session_id)).mappings().one()
        return JSONResponse(status_code=409, content=_full_project(current))

    db.commit()
    updated = db.execute(select(table).where(table.c.id == session_id)).mappings().one()
    return JSONResponse(status_code=200, content=_full_project(updated))
