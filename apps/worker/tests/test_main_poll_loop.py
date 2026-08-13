"""Real-PostgreSQL test proving `main._process_one` actually wires
claim_queued -> derive_requirements -> stage_success/stage_failure together
(Story 3.5 review finding: the primitives existed but nothing in the
shipped worker process called them in sequence — this is that missing
wiring, now covered).
"""

from __future__ import annotations

import os
import time
import uuid

import psycopg
import pytest

from src import main
from src.adapters import ollama_analysis, preparation_claim
from src.config import Settings
from src.domain.analysis_provider import AnalysisProviderError, FailureReason
from src.domain.requirement_derivation import RequirementProposal

WORKER_DATABASE_URL = os.environ.get("WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not WORKER_DATABASE_URL,
    reason="WORKER_DATABASE_URL not set; requires a live PostgreSQL instance with gateway migrations applied",
)


@pytest.fixture()
def conn():
    connection = psycopg.connect(WORKER_DATABASE_URL, autocommit=True)
    connection.execute("TRUNCATE TABLE start_preparations, analysis_sessions RESTART IDENTITY CASCADE")
    yield connection
    connection.close()


def _seed_session_and_preparation(conn, *, attempt: int = 1) -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    prep_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO analysis_sessions
            (id, creator_issuer, creator_subject, status, created_at, job_description_text, job_description_version)
        VALUES (%s, 'local', %s, 'preparing_to_start', now(), 'A sufficiently long job description text.', 1)
        """,
        (session_id, f"subject-{session_id}"),
    )
    conn.execute(
        """
        INSERT INTO start_preparations
            (id, analysis_session_id, status, job_description_version, document_versions,
             idempotency_key, request_fingerprint, created_at, attempt)
        VALUES (%s, %s, 'queued', 1, '{}', 'idem-1', 'fp-1', now(), %s)
        """,
        (prep_id, session_id, attempt),
    )
    return session_id, prep_id


def _settings() -> Settings:
    return Settings(
        database_url=WORKER_DATABASE_URL, ollama_host="http://ollama.invalid:11434", azure_openai_configured=False
    )


def test_process_one_returns_false_when_nothing_queued(conn):
    assert main._process_one(conn, _settings()) is False


def test_process_one_success_path_stages_validated(conn, monkeypatch):
    _, prep_id = _seed_session_and_preparation(conn)

    def fake_derive(job_description_text, *, base_url, model=None, timeout=60.0):
        return RequirementProposal(items=[])

    monkeypatch.setattr(ollama_analysis, "derive_requirements", fake_derive)

    claimed = main._process_one(conn, _settings())
    assert claimed is True
    status = conn.execute("SELECT status FROM start_preparations WHERE id = %s", (prep_id,)).fetchone()[0]
    assert status == "validated"


def test_process_one_failure_path_requeues(conn, monkeypatch):
    session_id, prep_id = _seed_session_and_preparation(conn, attempt=1)

    def fake_derive(job_description_text, *, base_url, model=None, timeout=60.0):
        raise AnalysisProviderError(FailureReason.TIMEOUT)

    monkeypatch.setattr(ollama_analysis, "derive_requirements", fake_derive)

    claimed = main._process_one(conn, _settings())
    assert claimed is True
    row = conn.execute("SELECT status, attempt FROM start_preparations WHERE id = %s", (prep_id,)).fetchone()
    assert row == ("queued", 2)


def test_process_one_terminal_failure_unlocks_session(conn, monkeypatch):
    session_id, prep_id = _seed_session_and_preparation(conn, attempt=2)

    def fake_derive(job_description_text, *, base_url, model=None, timeout=60.0):
        raise AnalysisProviderError(FailureReason.TIMEOUT)

    monkeypatch.setattr(ollama_analysis, "derive_requirements", fake_derive)

    claimed = main._process_one(conn, _settings())
    assert claimed is True
    prep_status = conn.execute("SELECT status FROM start_preparations WHERE id = %s", (prep_id,)).fetchone()[0]
    assert prep_status == "failed"
    session_status = conn.execute(
        "SELECT status FROM analysis_sessions WHERE id = %s", (session_id,)
    ).fetchone()[0]
    assert session_status == "draft"


def test_heartbeat_keeps_lease_alive_across_a_slow_provider_call(conn, monkeypatch):
    """A short lease (1s) with a fast heartbeat (0.3s) and a fake provider
    call slower than the original lease (1.5s) — without the heartbeat
    thread, the lease would expire mid-call and `stage_success`'s fencing
    predicate would reject the write (AD-6: processing occurs outside claim
    transactions, but the lease must survive that processing)."""
    _, prep_id = _seed_session_and_preparation(conn)
    monkeypatch.setattr(preparation_claim, "LEASE_SECONDS", 1)
    monkeypatch.setattr(main, "HEARTBEAT_SECONDS", 0.3)

    def slow_derive(job_description_text, *, base_url, model=None, timeout=60.0):
        time.sleep(1.5)
        return RequirementProposal(items=[])

    monkeypatch.setattr(ollama_analysis, "derive_requirements", slow_derive)

    claimed = main._process_one(conn, _settings())
    assert claimed is True
    status = conn.execute("SELECT status FROM start_preparations WHERE id = %s", (prep_id,)).fetchone()[0]
    assert status == "validated"
