"""AD-3 creator-ownership check — reusable object-level authorization primitive.

Every descendant repository (Documents, Analysis Sessions, Candidate
Results, Reports, Shortlist, Question Sets, print views, ...) must look up
its row and confirm the owning Recruiter's immutable subject matches the
requesting identity's subject, never trusting a parent check already ran.
`authorize_owned_row` is that check, factored out once so Epic 3+ stories
reuse it instead of re-deriving the IDOR-safe shape per feature (proven
generically against fixture objects by Story 1.5c).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Column, Table, select
from sqlalchemy.orm import Session as OrmSession

from ..domain.identity import Identity


def authorize_owned_row(
    db: OrmSession,
    identity: Identity,
    table: Table,
    id_column: Column,
    owner_column: Column,
    object_id: str,
) -> dict[str, Any] | None:
    """Return the row if `identity.subject` owns it; `None` otherwise.

    AD-3: "Missing, tampered, and cross-owner identifiers share one
    resource-unavailable response shape." Returning `None` uniformly for a
    nonexistent row and for an existing row owned by someone else is what
    makes that guarantee possible — callers cannot distinguish the two
    cases from the return value alone.

    `id_column`/`owner_column` must belong to `table` — a mismatched column
    from another table would silently authorize against the wrong data, so
    this fails loudly (`ValueError`) rather than risk that misuse.

    Returns the full row as-is; a caller serializing it into a response must
    project only the fields it means to expose (nothing here allowlists
    columns for a future table that may carry sensitive/internal fields).
    """
    if id_column.table is not table or owner_column.table is not table:
        raise ValueError("id_column and owner_column must belong to table")
    row = db.execute(select(table).where(id_column == object_id)).mappings().one_or_none()
    if row is None or row[owner_column.name] != identity.subject:
        return None
    return dict(row)
