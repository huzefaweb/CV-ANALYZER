"""Story 3.5, Task 8: the end-to-end API-layer lifecycle test the story
spec named — drives POST /new-analysis/{id}/analyze (3.4) through to a
frozen session via scan_and_finalize (3.5, worker's role simulated by
directly seeding the validated proposal, since the worker runs
out-of-process and this test does not spin one up), then confirms
POST /new-analysis (3.4's reconstruction path) reflects the frozen state.

Mirrors test_analyze_api.py's live-DATABASE_URL-skip pattern and fixture
conventions (kept self-contained per this codebase's established per-file
convention rather than importing another test module's helpers).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient

from src.adapters import local_identity
from src.adapters.api import app
from src.adapters.models import StartPreparation
from src.adapters.preparation_finalizer import scan_and_finalize

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


def test_analyze_then_finalize_is_reflected_by_reconstruction(db_session):
    _, token = _admitted_identity_and_token(db_session)

    draft = client.post("/new-analysis", cookies={"session": token})
    assert draft.status_code == 201, draft.text
    session_id = draft.json()["id"]

    save = client.put(
        f"/new-analysis/{session_id}",
        json={"job_description_text": VALID_JOB_DESCRIPTION, "expected_version": 0},
        cookies={"session": token},
    )
    assert save.status_code == 200, save.text

    upload = client.post(
        f"/new-analysis/{session_id}/documents",
        files={"file": ("resume.pdf", valid_pdf_bytes(), "application/octet-stream")},
        data={"idempotency_key": str(uuid.uuid4())},
        cookies={"session": token},
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]

    analyze = client.post(
        f"/new-analysis/{session_id}/analyze",
        json={
            "expected_job_description_version": 1,
            "expected_document_versions": {document_id: 1},
            "idempotency_key": str(uuid.uuid4()),
        },
        cookies={"session": token},
    )
    assert analyze.status_code == 202, analyze.text

    reconstructed_locked = client.post("/new-analysis", cookies={"session": token})
    assert reconstructed_locked.status_code == 200
    assert reconstructed_locked.json()["status"] == "preparing_to_start"
    assert reconstructed_locked.json()["preparation"]["status"] == "queued"

    # Simulate the worker: stage a schema-valid proposal directly, as the
    # story's Task 8 spec instructs (the worker runs out-of-process).
    prep_table = StartPreparation.__table__
    db_session.execute(
        prep_table.update()
        .where(prep_table.c.analysis_session_id == session_id)
        .values(
            status="validated",
            proposal_json={
                "items": [
                    {
                        "component": "mandatory_skills",
                        "classification": "mandatory",
                        "text": "Python",
                        "source_start": 0,
                        "source_end": 6,
                    }
                ]
            },
        )
    )
    db_session.commit()

    claimed = scan_and_finalize(db_session)
    assert claimed is True

    reconstructed_frozen = client.post("/new-analysis", cookies={"session": token})
    assert reconstructed_frozen.status_code == 200
    body = reconstructed_frozen.json()
    assert body["status"] == "frozen_inputs"
    assert body["preparation"]["status"] == "frozen"
