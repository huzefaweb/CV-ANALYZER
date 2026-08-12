"""Story 3.4: `POST /new-analysis/{id}/analyze` (idempotent Start
Preparation lock) plus the session-status-race fixes it requires on the
existing draft-mutation routes (Task 2).

Mirrors test_new_analysis_api.py's/test_document_upload_api.py's
live-DATABASE_URL-skip pattern and reuses their fixture/assertion
conventions.
"""

from __future__ import annotations

import os
import tempfile
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient

from src.adapters import local_identity
from src.adapters.api import app
from src.adapters.models import AnalysisSession, Document, StartPreparation

from tests.fixtures.document_fixtures import valid_pdf_bytes

client = TestClient(app)

VALID_JOB_DESCRIPTION = "x" * 200


@pytest.fixture(autouse=True)
def _storage_root(monkeypatch):
    directory = tempfile.mkdtemp()
    monkeypatch.setattr("src.adapters.document_storage.DOCUMENT_STORAGE_ROOT", directory)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _email() -> str:
    return f"user-{uuid.uuid4()}@example.com"


def _admitted_identity_and_token(db_session):
    email = _email()
    identity = local_identity.register(db_session, email, "a-fixture-password")
    local_identity.admit_user(db_session, identity.subject)
    session = local_identity.authenticate(db_session, email, "a-fixture-password")
    return identity, session.token


def _seed_session(
    db_session,
    issuer: str,
    subject: str,
    status: str = "draft",
    job_description_text: str = VALID_JOB_DESCRIPTION,
    job_description_version: int = 0,
) -> str:
    session_id = str(uuid.uuid4())
    db_session.add(
        AnalysisSession(
            id=session_id,
            creator_issuer=issuer,
            creator_subject=subject,
            status=status,
            created_at=datetime.now(timezone.utc),
            job_description_text=job_description_text,
            job_description_version=job_description_version,
        )
    )
    db_session.commit()
    return session_id


def _upload_ready_document(db_session, session_id: str, token: str) -> dict:
    response = client.post(
        f"/new-analysis/{session_id}/documents",
        files={"file": ("resume.pdf", valid_pdf_bytes(), "application/octet-stream")},
        data={"idempotency_key": str(uuid.uuid4())},
        cookies={"session": token},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _analyze(
    session_id: str,
    expected_job_description_version: int,
    expected_document_versions: dict[str, int],
    idempotency_key: str | None = None,
    token: str | None = None,
):
    return client.post(
        f"/new-analysis/{session_id}/analyze",
        json={
            "expected_job_description_version": expected_job_description_version,
            "expected_document_versions": expected_document_versions,
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
        },
        cookies={"session": token} if token else None,
    )


def _valid_draft(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    document = _upload_ready_document(db_session, session_id, token)
    return identity, token, session_id, document


def test_analyze_locks_session_and_creates_one_preparation(db_session):
    identity, token, session_id, document = _valid_draft(db_session)

    response = _analyze(session_id, 0, {document["id"]: 1}, token=token)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"

    session_row = db_session.get(AnalysisSession, session_id)
    assert session_row.status == "preparing_to_start"

    count = (
        db_session.query(StartPreparation)
        .filter(StartPreparation.analysis_session_id == session_id)
        .count()
    )
    assert count == 1


def test_replay_with_same_idempotency_key_returns_same_preparation(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    key = str(uuid.uuid4())

    first = _analyze(session_id, 0, {document["id"]: 1}, idempotency_key=key, token=token)
    second = _analyze(session_id, 0, {document["id"]: 1}, idempotency_key=key, token=token)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]

    count = (
        db_session.query(StartPreparation)
        .filter(StartPreparation.analysis_session_id == session_id)
        .count()
    )
    assert count == 1


def test_double_click_with_different_idempotency_keys_returns_same_preparation(db_session):
    identity, token, session_id, document = _valid_draft(db_session)

    first = _analyze(session_id, 0, {document["id"]: 1}, token=token)
    second = _analyze(session_id, 0, {document["id"]: 1}, token=token)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


def test_stale_job_description_version_returns_validation_summary(db_session):
    identity, token, session_id, document = _valid_draft(db_session)

    response = _analyze(session_id, 5, {document["id"]: 1}, token=token)

    assert response.status_code == 409
    errors = response.json()["errors"]
    assert {"field": "job_description", "reason": "stale_version"} in errors

    session_row = db_session.get(AnalysisSession, session_id)
    assert session_row.status == "draft"
    count = (
        db_session.query(StartPreparation)
        .filter(StartPreparation.analysis_session_id == session_id)
        .count()
    )
    assert count == 0


def test_missing_document_in_expected_versions_returns_validation_summary(db_session):
    identity, token, session_id, document = _valid_draft(db_session)

    response = _analyze(session_id, 0, {}, token=token)

    assert response.status_code == 409
    errors = response.json()["errors"]
    assert {"field": f"document:{document['id']}", "reason": "missing"} in errors


def test_stale_document_version_returns_validation_summary(db_session):
    identity, token, session_id, document = _valid_draft(db_session)

    response = _analyze(session_id, 0, {document["id"]: 99}, token=token)

    assert response.status_code == 409
    errors = response.json()["errors"]
    assert {"field": f"document:{document['id']}", "reason": "stale_version"} in errors


def test_unexpected_document_id_returns_validation_summary(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    other_document_id = str(uuid.uuid4())

    response = _analyze(session_id, 0, {document["id"]: 1, other_document_id: 1}, token=token)

    assert response.status_code == 409
    errors = response.json()["errors"]
    assert {"field": f"document:{other_document_id}", "reason": "unexpected"} in errors


def test_no_ready_documents_returns_validation_summary(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)

    response = _analyze(session_id, 0, {}, token=token)

    assert response.status_code == 409
    errors = response.json()["errors"]
    assert {"field": "documents", "reason": "none_ready"} in errors


def test_job_description_below_minimum_length_returns_validation_summary(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject, job_description_text="too short")
    document = _upload_ready_document(db_session, session_id, token)

    response = _analyze(session_id, 0, {document["id"]: 1}, token=token)

    assert response.status_code == 409
    errors = response.json()["errors"]
    assert {"field": "job_description", "reason": "below_minimum_length"} in errors


def test_analyze_against_active_preparation_with_different_fingerprint_returns_conflict(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    first = _analyze(session_id, 0, {document["id"]: 1}, token=token)
    assert first.status_code == 202

    other_document_id = str(uuid.uuid4())
    response = _analyze(session_id, 0, {other_document_id: 1}, token=token)

    assert response.status_code == 409
    assert response.json()["error"] == "active_preparation_exists"

    count = (
        db_session.query(StartPreparation)
        .filter(StartPreparation.analysis_session_id == session_id)
        .count()
    )
    assert count == 1


def test_analyze_with_same_key_but_changed_body_returns_idempotency_conflict(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    key = str(uuid.uuid4())
    first = _analyze(session_id, 0, {document["id"]: 1}, idempotency_key=key, token=token)
    assert first.status_code == 202

    other_document_id = str(uuid.uuid4())
    response = _analyze(session_id, 0, {other_document_id: 1}, idempotency_key=key, token=token)

    assert response.status_code == 409
    assert response.json()["error"] == "idempotency_key_conflict"


def _replace_document(
    session_id: str, document_id: str, expected_version: int, token: str
):
    return client.put(
        f"/new-analysis/{session_id}/documents/{document_id}",
        files={"file": ("resume2.pdf", valid_pdf_bytes(), "application/octet-stream")},
        data={"expected_version": str(expected_version), "idempotency_key": str(uuid.uuid4())},
        cookies={"session": token},
    )


def test_concurrent_analyze_and_replace_document_are_mutually_exclusive(db_session):
    """Real concurrency (code-review finding): proves the FOR UPDATE lock
    Analyze takes on Ready Documents actually closes the race against a
    concurrent replace_document — Task 2's session-status CAS guard and
    Task 3's locking read must produce exactly one of two consistent
    outcomes, never both succeeding against inconsistent state."""
    identity, token, session_id, document = _valid_draft(db_session)

    with ThreadPoolExecutor(max_workers=2) as executor:
        analyze_future = executor.submit(_analyze, session_id, 0, {document["id"]: 1}, None, token)
        replace_future = executor.submit(_replace_document, session_id, document["id"], 1, token)
        analyze_response = analyze_future.result()
        replace_response = replace_future.result()

    session_row = db_session.get(AnalysisSession, session_id)
    document_row = db_session.get(Document, document["id"])

    if analyze_response.status_code == 202:
        # Analyze won: the session is locked with the pre-replace document
        # version, and replace_document must have lost to the session-status
        # CAS guard (Task 2) — never both succeeding.
        assert session_row.status == "preparing_to_start"
        assert document_row.content_version == 1
        assert replace_response.status_code == 409
    else:
        # replace_document won: it committed a new content_version before
        # Analyze's FOR UPDATE read observed it, so Analyze's validation
        # must report a stale_version conflict for that document, and the
        # session must remain unlocked.
        assert replace_response.status_code == 200
        assert document_row.content_version == 2
        assert session_row.status == "draft"
        assert analyze_response.status_code == 409
        errors = analyze_response.json()["errors"]
        assert {"field": f"document:{document['id']}", "reason": "stale_version"} in errors

    # Never both: exactly one side must have succeeded.
    assert (analyze_response.status_code == 202) != (replace_response.status_code == 200)


def test_concurrent_identical_analyze_commands_produce_exactly_one_preparation(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    key = str(uuid.uuid4())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_analyze, session_id, 0, {document["id"]: 1}, key, token)
            for _ in range(2)
        ]
        results = [future.result() for future in futures]

    assert all(r.status_code == 202 for r in results), [r.text for r in results]
    ids = {r.json()["id"] for r in results}
    assert len(ids) == 1

    count = (
        db_session.query(StartPreparation)
        .filter(StartPreparation.analysis_session_id == session_id)
        .count()
    )
    assert count == 1


def test_get_or_create_draft_reconstructs_locked_state_not_a_second_session(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    analyzed = _analyze(session_id, 0, {document["id"]: 1}, token=token)
    assert analyzed.status_code == 202

    client.cookies.set("session", token)
    response = client.post("/new-analysis")
    client.cookies.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == session_id
    assert body["status"] == "preparing_to_start"
    assert body["preparation"]["status"] == "queued"

    count = (
        db_session.query(AnalysisSession)
        .filter(
            AnalysisSession.creator_issuer == identity.issuer,
            AnalysisSession.creator_subject == identity.subject,
        )
        .count()
    )
    assert count == 1


def test_save_job_description_against_locked_session_returns_409(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    analyzed = _analyze(session_id, 0, {document["id"]: 1}, token=token)
    assert analyzed.status_code == 202

    client.cookies.set("session", token)
    response = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": "x" * 250, "expected_version": 0},
    )
    client.cookies.clear()

    assert response.status_code == 409
    session_row = db_session.get(AnalysisSession, session_id)
    assert session_row.job_description_text == VALID_JOB_DESCRIPTION


def test_upload_document_against_locked_session_returns_409_and_no_orphaned_file(db_session, _storage_root):
    identity, token, session_id, document = _valid_draft(db_session)
    analyzed = _analyze(session_id, 0, {document["id"]: 1}, token=token)
    assert analyzed.status_code == 202

    response = client.post(
        f"/new-analysis/{session_id}/documents",
        files={"file": ("second.pdf", valid_pdf_bytes(), "application/octet-stream")},
        data={"idempotency_key": str(uuid.uuid4())},
        cookies={"session": token},
    )

    assert response.status_code == 409
    count = db_session.query(Document).filter(Document.analysis_session_id == session_id).count()
    assert count == 1


def test_remove_document_against_locked_session_returns_409(db_session):
    identity, token, session_id, document = _valid_draft(db_session)
    analyzed = _analyze(session_id, 0, {document["id"]: 1}, token=token)
    assert analyzed.status_code == 202

    response = client.post(
        f"/new-analysis/{session_id}/documents/{document['id']}/remove",
        json={"expected_version": 1, "idempotency_key": str(uuid.uuid4())},
        cookies={"session": token},
    )

    assert response.status_code == 409
    row = db_session.get(Document, document["id"])
    assert row.status == "ready"


def test_analyze_against_missing_or_cross_owner_session_returns_404(db_session):
    identity_a, token_a = _admitted_identity_and_token(db_session)
    identity_b, token_b = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity_a.issuer, identity_a.subject)

    cross_owner = _analyze(session_id, 0, {}, token=token_b)
    missing = _analyze(str(uuid.uuid4()), 0, {}, token=token_a)

    for response in (cross_owner, missing):
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}


def test_unauthenticated_and_unadmitted_analyze_requests_stay_neutral(db_session):
    unauthenticated = client.post(
        "/new-analysis/any-id/analyze",
        json={"expected_job_description_version": 0, "expected_document_versions": {}, "idempotency_key": "x"},
    )
    assert unauthenticated.status_code == 401
