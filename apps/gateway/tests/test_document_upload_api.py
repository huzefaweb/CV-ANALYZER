"""Story 3.2: `POST /new-analysis/{id}/documents` (streamed, independently
validated Resume upload) and `GET /new-analysis/{id}/documents` (list).

Mirrors test_new_analysis_api.py's live-DATABASE_URL-skip pattern and
reuses its fixture/assertion conventions.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient

from src.adapters import local_identity
from src.adapters.api import app
from src.adapters.models import AnalysisSession, Document

from tests.fixtures.document_fixtures import (
    corrupt_docx_bytes,
    corrupt_pdf_bytes,
    disguised_extension_bytes,
    oversized_bytes,
    password_protected_docx_bytes,
    valid_docx_bytes,
    valid_pdf_bytes,
    zip_bomb_docx_bytes,
)

client = TestClient(app)


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


def _seed_session(db_session, issuer: str, subject: str, status: str = "draft") -> str:
    session_id = str(uuid.uuid4())
    db_session.add(
        AnalysisSession(
            id=session_id,
            creator_issuer=issuer,
            creator_subject=subject,
            status=status,
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    return session_id


def _upload(session_id: str, filename: str, data: bytes, idempotency_key: str | None = None, content_type: str = "application/octet-stream"):
    return client.post(
        f"/new-analysis/{session_id}/documents",
        files={"file": (filename, data, content_type)},
        data={"idempotency_key": idempotency_key or str(uuid.uuid4())},
    )


def test_upload_valid_pdf_and_docx_get_distinct_references(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)

    pdf_response = _upload(session_id, "resume.pdf", valid_pdf_bytes())
    docx_response = _upload(session_id, "resume.docx", valid_docx_bytes())
    client.cookies.clear()

    assert pdf_response.status_code == 201
    assert docx_response.status_code == 201
    pdf_body = pdf_response.json()
    docx_body = docx_response.json()
    assert pdf_body["content_version"] == 1
    assert docx_body["content_version"] == 1
    assert pdf_body["document_reference"] != docx_body["document_reference"]
    assert pdf_body["status"] == "ready"


def test_capacity_accepts_20_and_rejects_the_21st(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)

    for i in range(20):
        response = _upload(session_id, f"resume-{i}.pdf", valid_pdf_bytes())
        assert response.status_code == 201, response.text

    excess_response = _upload(session_id, "resume-20.pdf", valid_pdf_bytes())
    client.cookies.clear()

    assert excess_response.status_code == 422
    assert excess_response.json()["category"] == "count_exceeded"

    listing = client.get(f"/new-analysis/{session_id}/documents", cookies={"session": token})
    assert len(listing.json()["documents"]) == 20


def test_identical_filenames_remain_separate_documents(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)

    first = _upload(session_id, "resume.pdf", valid_pdf_bytes())
    second = _upload(session_id, "resume.pdf", valid_pdf_bytes())
    client.cookies.clear()

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["document_reference"] != second.json()["document_reference"]


def test_oversized_filename_or_idempotency_key_is_rejected_cleanly(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)

    long_filename = ("a" * 260) + ".pdf"
    by_filename = _upload(session_id, long_filename, valid_pdf_bytes())
    by_key = _upload(session_id, "resume.pdf", valid_pdf_bytes(), idempotency_key="k" * 200)
    client.cookies.clear()

    assert by_filename.status_code == 422
    assert by_filename.json()["category"] == "invalid_request"
    assert by_key.status_code == 422
    assert by_key.json()["category"] == "invalid_request"

    count = db_session.query(Document).filter(Document.analysis_session_id == session_id).count()
    assert count == 0


def test_concurrent_uploads_with_the_same_idempotency_key_produce_exactly_one_document(db_session):
    """Real concurrency, not a sequential replay — proves AC#3's idempotency
    guarantee holds under a true race (review finding: the loser must get
    the winner's Document back, not a generic conflict), and that the
    loser's orphaned bytes are cleaned up rather than leaked."""
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)
    key = str(uuid.uuid4())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_upload, session_id, "resume.pdf", valid_pdf_bytes(), key) for _ in range(2)]
        responses = [f.result() for f in futures]
    client.cookies.clear()

    assert {r.status_code for r in responses} <= {200, 201}
    document_ids = {r.json()["id"] for r in responses}
    assert len(document_ids) == 1, "both requests must resolve to the same Document"

    count = db_session.query(Document).filter(Document.analysis_session_id == session_id).count()
    assert count == 1


@pytest.mark.parametrize(
    "filename,data,expected_category",
    [
        ("resume.pdf", corrupt_pdf_bytes(), "corrupt_container"),
        ("resume.docx", password_protected_docx_bytes(), "signature_mismatch"),
        ("resume.docx", corrupt_docx_bytes(), "corrupt_container"),
        ("resume.docx", zip_bomb_docx_bytes(), "archive_expansion"),
        ("resume.pdf", disguised_extension_bytes(), "signature_mismatch"),
        ("resume.exe", valid_pdf_bytes(), "extension_rejected"),
    ],
    ids=[
        "corrupt-pdf",
        "password-protected-docx",
        "corrupt-docx",
        "zip-bomb-docx",
        "disguised-extension",
        "wrong-extension",
    ],
)
def test_rejection_categories_persist_no_row_and_no_bytes(db_session, _storage_root, filename, data, expected_category):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)

    response = _upload(session_id, filename, data)
    client.cookies.clear()

    assert response.status_code == 422
    assert response.json()["category"] == expected_category

    count = db_session.query(Document).filter(Document.analysis_session_id == session_id).count()
    assert count == 0
    assert os.listdir(_storage_root) == []


def test_oversized_file_is_rejected_with_size_limit(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)

    response = _upload(session_id, "resume.pdf", oversized_bytes(10 * 1024 * 1024 + 1))
    client.cookies.clear()

    assert response.status_code == 422
    assert response.json()["category"] == "size_limit"
    count = db_session.query(Document).filter(Document.analysis_session_id == session_id).count()
    assert count == 0


def test_replayed_idempotency_key_returns_existing_document_without_duplicate(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)
    client.cookies.set("session", token)

    key = str(uuid.uuid4())
    first = _upload(session_id, "resume.pdf", valid_pdf_bytes(), idempotency_key=key)
    second = _upload(session_id, "resume.pdf", valid_pdf_bytes(), idempotency_key=key)
    client.cookies.clear()

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["document_reference"] == second.json()["document_reference"]

    count = db_session.query(Document).filter(Document.analysis_session_id == session_id).count()
    assert count == 1


def test_upload_against_locked_session_returns_409(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject, status="preparing")
    client.cookies.set("session", token)

    response = _upload(session_id, "resume.pdf", valid_pdf_bytes())
    client.cookies.clear()

    assert response.status_code == 409


def test_upload_against_missing_or_cross_owner_session_returns_neutral_404(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    other_identity, _ = _admitted_identity_and_token(db_session)
    other_session_id = _seed_session(db_session, other_identity.issuer, other_identity.subject)
    client.cookies.set("session", token)

    missing = _upload(str(uuid.uuid4()), "resume.pdf", valid_pdf_bytes())
    cross_owner = _upload(other_session_id, "resume.pdf", valid_pdf_bytes())
    client.cookies.clear()

    assert missing.status_code == 404
    assert cross_owner.status_code == 404


def test_upload_unauthenticated_is_401():
    response = _upload(str(uuid.uuid4()), "resume.pdf", valid_pdf_bytes())
    assert response.status_code == 401


def test_upload_not_admitted_is_403(db_session):
    email = _email()
    identity = local_identity.register(db_session, email, "a-fixture-password")
    session = local_identity.authenticate(db_session, email, "a-fixture-password")
    client.cookies.set("session", session.token)

    response = _upload(str(uuid.uuid4()), "resume.pdf", valid_pdf_bytes())
    client.cookies.clear()

    assert response.status_code == 403


def test_list_documents_returns_empty_for_fresh_draft(db_session):
    identity, token = _admitted_identity_and_token(db_session)
    session_id = _seed_session(db_session, identity.issuer, identity.subject)

    response = client.get(f"/new-analysis/{session_id}/documents", cookies={"session": token})

    assert response.status_code == 200
    assert response.json()["documents"] == []
