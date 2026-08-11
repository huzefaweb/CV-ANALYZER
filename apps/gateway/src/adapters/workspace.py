"""Workspace entry projection (AD-3/AR-8): the latest creator-owned
Analysis Session, and neutral direct-object access to one by id.

Reuses require_admitted_identity (AR-6, Story 1.2/2.2) and authorize_owned_row
(AD-3, Story 1.5c) as-is — this module is their first real (non-fixture-only)
caller, not a reason to write new admission or ownership primitives.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..domain.identity import Identity
from .authorization import authorize_owned_row
from .db import get_db
from .identity import require_admitted_identity
from .models import AnalysisSession

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
