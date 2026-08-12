"""Gateway preparation coordinator (Story 3.5, AD-4, AD-7, AR-11, AR-18):
a stateless, level-triggered scan that claims one validated Start
Preparation, rechecks every AD-4-named condition, and atomically freezes
Job Requirements, Scoring Configuration, Revision 1, Candidate rows,
membership shells, and Candidate jobs — or terminates/unlocks on any
failure. No lease/token — a short transaction-scoped `FOR UPDATE SKIP
LOCKED` claim is the whole reauthorization mechanism (AD-7).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..domain.requirement_canonicalization import (
    RequirementConflictError,
    assign_display_ids,
    merge_duplicates,
    parse_and_validate_proposal,
)
from ..domain.scoring_configuration import applicable_components, effective_weights_bps
from .models import (
    AnalysisRevision,
    AnalysisSession,
    Candidate,
    CandidateJob,
    Document,
    JobRequirement,
    RevisionMembership,
    ScoringConfiguration,
    StartPreparation,
)


def _terminate_and_unlock(db: OrmSession, preparation_id: str, session_id: str, reason: str) -> None:
    """CAS-terminate the preparation and unlock the session (AC#2). No
    revision/membership/scoring row exists yet at this point in the flow —
    nothing else to roll back."""
    prep_table = StartPreparation.__table__
    session_table = AnalysisSession.__table__
    db.execute(
        prep_table.update()
        .where(prep_table.c.id == preparation_id)
        .where(prep_table.c.status == "validated")
        .values(status="failed", failure_reason=reason)
    )
    db.execute(
        session_table.update()
        .where(session_table.c.id == session_id)
        .where(session_table.c.status == "preparing_to_start")
        .values(status="draft")
    )
    db.commit()


def scan_and_finalize(db: OrmSession) -> bool:
    """Claims and finalizes (or terminates) at most one validated
    preparation. Returns True if a row was claimed (whether it froze or
    terminated), False if there was nothing to do."""
    prep_table = StartPreparation.__table__
    claimed = (
        db.execute(
            select(prep_table)
            .where(prep_table.c.status == "validated")
            .order_by(prep_table.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if claimed is None:
        db.rollback()
        return False

    preparation_id = claimed["id"]
    session_id = claimed["analysis_session_id"]

    session_table = AnalysisSession.__table__
    # Locked for the rest of this transaction (review finding: a plain SELECT
    # left a window for a concurrent worker terminal-failure unlock or a
    # hypothetical future mutator to change session status between this read
    # and the final CAS below without being detected).
    session_row = (
        db.execute(select(session_table).where(session_table.c.id == session_id).with_for_update())
        .mappings()
        .one_or_none()
    )
    if session_row is None or session_row["status"] != "preparing_to_start":
        _terminate_and_unlock(db, preparation_id, session_id, "session_not_preparing")
        return True

    if session_row["job_description_version"] != claimed["job_description_version"]:
        _terminate_and_unlock(db, preparation_id, session_id, "stale_job_description_version")
        return True

    documents_table = Document.__table__
    ready_documents = (
        db.execute(
            select(documents_table)
            .where(documents_table.c.analysis_session_id == session_id)
            .where(documents_table.c.status == "ready")
            .order_by(documents_table.c.id)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    snapshot_versions: dict[str, int] = claimed["document_versions"] or {}
    live_versions = {d["id"]: d["content_version"] for d in ready_documents}
    if live_versions != snapshot_versions:
        _terminate_and_unlock(db, preparation_id, session_id, "stale_document_versions")
        return True

    try:
        proposed = parse_and_validate_proposal(claimed["proposal_json"], session_row["job_description_text"])
    except ValueError:
        _terminate_and_unlock(db, preparation_id, session_id, "invalid_proposal_schema")
        return True

    try:
        canonical = merge_duplicates(proposed)
    except RequirementConflictError:
        _terminate_and_unlock(db, preparation_id, session_id, "conflicting_requirements")
        return True

    display_requirements = assign_display_ids(canonical)

    components_present = {requirement.component for _, requirement in display_requirements}
    applicable = applicable_components(components_present)
    try:
        weights = effective_weights_bps(applicable)
    except ValueError:
        _terminate_and_unlock(db, preparation_id, session_id, "no_applicable_requirements")
        return True

    now = datetime.now(timezone.utc)

    for display_id, requirement in display_requirements:
        db.add(
            JobRequirement(
                id=str(uuid.uuid4()),
                analysis_session_id=session_id,
                display_id=display_id,
                component=requirement.component,
                classification=requirement.classification,
                canonical_text=requirement.canonical_text,
                source_locators=requirement.source_locators,
                created_at=now,
            )
        )

    for component, is_applicable in applicable.items():
        db.add(
            ScoringConfiguration(
                id=str(uuid.uuid4()),
                analysis_session_id=session_id,
                component=component.value,
                applicable=is_applicable,
                effective_weight_bps=weights.get(component, 0),
                created_at=now,
            )
        )

    # Unreachable via the normal Analyze flow (3.4 already requires >=1
    # Ready Document before locking) but defended anyway (review finding):
    # freezing a Revision with zero Candidates would publish a comparison
    # with nothing to score/rank.
    if not ready_documents:
        _terminate_and_unlock(db, preparation_id, session_id, "no_ready_documents")
        return True

    candidate_ids: list[str] = []
    for document in ready_documents:
        candidate_id = str(uuid.uuid4())
        candidate_ids.append(candidate_id)
        db.add(
            Candidate(
                id=candidate_id,
                analysis_session_id=session_id,
                document_id=document["id"],
                document_reference=document["document_reference"],
                created_at=now,
            )
        )

    prep_cas = db.execute(
        prep_table.update()
        .where(prep_table.c.id == preparation_id)
        .where(prep_table.c.status == "validated")
        .values(status="frozen")
    )
    session_cas = db.execute(
        session_table.update()
        .where(session_table.c.id == session_id)
        .where(session_table.c.status == "preparing_to_start")
        .values(status="frozen_inputs")
    )
    if prep_cas.rowcount != 1 or session_cas.rowcount != 1:
        # Defense in depth (review finding): the FOR UPDATE locks above
        # should make this unreachable, but if it ever fires, abort the
        # whole transaction rather than commit a partial freeze — the
        # preparation stays "validated" and the next scan retries cleanly.
        db.rollback()
        return True

    revision_id = str(uuid.uuid4())
    db.add(
        AnalysisRevision(
            id=revision_id,
            analysis_session_id=session_id,
            revision_number=1,
            status="frozen",
            created_at=now,
        )
    )
    for candidate_id in candidate_ids:
        db.add(
            RevisionMembership(
                id=str(uuid.uuid4()),
                analysis_revision_id=revision_id,
                candidate_id=candidate_id,
                outcome="queued",
                created_at=now,
            )
        )
        db.add(
            CandidateJob(
                id=str(uuid.uuid4()),
                analysis_revision_id=revision_id,
                candidate_id=candidate_id,
                status="queued",
                created_at=now,
            )
        )

    db.commit()
    return True
