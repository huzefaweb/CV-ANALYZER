"""Test-only FastAPI app exercising the query/command/print descendant route
classes (AR-37/AR-38/AR-41) against Story 1.5c's fixture table.

Never mounted into the production `app` in src/adapters/api.py — these are
stand-in routes for "every planned descendant route/command class" AD-3
binds its rule to, not real Epic 3+ features. Reuses Story 1.2's real,
already-proven `require_admitted_identity` dependency for the
authentication/admission layer; only the object-ownership layer below it is
new in this story.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session as OrmSession

from src.adapters import api  # noqa: F401  (import runs init_engine() as a side effect)
from src.adapters.authorization import authorize_owned_row
from src.adapters.db import get_db
from src.adapters.identity import require_admitted_identity
from src.domain.identity import Identity

from tests.fixtures.authorization_matrix_schema import (
    authorization_matrix_fixture_object as _fixture_table,
)

app = FastAPI()

# One fixed shape for every object-level denial (missing or cross-owner),
# distinct from Story 1.2's 401 (invalid/expired/tampered session) and 403
# (unadmitted) — a different, later authorization layer with its own
# uniform shape per AD-3.
_NOT_FOUND = HTTPException(status_code=404, detail="Not found")


def _authorize_or_404(db: OrmSession, identity: Identity, object_id: str) -> dict:
    row = authorize_owned_row(
        db,
        identity,
        _fixture_table,
        _fixture_table.c.object_id,
        _fixture_table.c.owner_subject,
        object_id,
    )
    if row is None:
        raise _NOT_FOUND
    return row


@app.get("/harness/objects/{object_id}")
def query_object(
    object_id: str,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict:
    """Query/projection class (AR-38/AR-39)."""
    row = _authorize_or_404(db, identity, object_id)
    return {"object_id": row["object_id"]}


@app.post("/harness/objects/{object_id}/command", status_code=202)
def command_object(
    object_id: str,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict:
    """Long-command class (AR-37)."""
    _authorize_or_404(db, identity, object_id)
    return {"status": "accepted"}


@app.get("/harness/objects/{object_id}/print")
def print_object(
    object_id: str,
    identity: Identity = Depends(require_admitted_identity),
    db: OrmSession = Depends(get_db),
) -> dict:
    """Source/print-access class (AR-41/AR-42)."""
    row = _authorize_or_404(db, identity, object_id)
    return {"object_id": row["object_id"], "print": True}
