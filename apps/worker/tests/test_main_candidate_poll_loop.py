"""Real-PostgreSQL test proving `main._process_one_candidate` wires the
complete Story 4.4 attempt together: claim_queued -> read Document bytes ->
parse_resume -> stage_parse_success -> build_analysis_view/check_budget ->
propose/validate_locators -> stage_provider_success/stage_provider_failure.
Mirrors test_main_poll_loop.py's fixture/skip shape.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from fpdf import FPDF

from src import main
from src.adapters import candidate_claim, ollama_analysis
from src.config import Settings
from src.domain import analysis_view
from src.domain.analysis_provider import (
    AnalysisProposal,
    AnalysisProviderError,
    AnalysisState,
    FailureReason,
    ProposalItem,
)

WORKER_DATABASE_URL = os.environ.get("WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not WORKER_DATABASE_URL,
    reason="WORKER_DATABASE_URL not set; requires a live PostgreSQL instance with gateway migrations applied",
)

RESUME_TEXT = (
    "Backend engineer for 5 years, primarily in Python and Django.\n"
    "Built internal tools using PostgreSQL and Redis for caching.\n"
    "Mentored two junior engineers on the payments team."
)


def _pdf_bytes(text: str) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 10, text)
    return bytes(pdf.output())


@pytest.fixture()
def conn():
    connection = psycopg.connect(WORKER_DATABASE_URL, autocommit=True)
    connection.execute(
        "TRUNCATE TABLE candidate_jobs, parse_artifacts, candidate_identities, candidate_proposals, "
        "candidates, documents, job_requirements, analysis_revisions, analysis_sessions "
        "RESTART IDENTITY CASCADE"
    )
    yield connection
    connection.close()


def _seed_pipeline(conn, storage_root, *, attempt: int = 1) -> tuple[str, str]:
    """Seeds a full frozen pipeline: session -> job_requirements ->
    revision -> document -> candidate -> queued candidate_job. Returns
    (job_id, candidate_id)."""
    session_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO analysis_sessions
            (id, creator_issuer, creator_subject, status, created_at, job_description_text, job_description_version)
        VALUES (%s, 'local', %s, 'frozen_inputs', now(), 'A sufficiently long job description text.', 1)
        """,
        (session_id, f"subject-{session_id}"),
    )
    conn.execute(
        """
        INSERT INTO job_requirements
            (id, analysis_session_id, display_id, component, classification, canonical_text, source_locators, created_at)
        VALUES (%s, %s, 'JR-001', 'mandatory_skills', 'mandatory', 'Python backend experience', '[]', now())
        """,
        (str(uuid.uuid4()), session_id),
    )
    revision_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO analysis_revisions (id, analysis_session_id, revision_number, status, created_at)
        VALUES (%s, %s, 1, 'frozen', now())
        """,
        (revision_id, session_id),
    )
    document_id = str(uuid.uuid4())
    storage_path = os.path.join(storage_root, "resume.pdf")
    with open(storage_path, "wb") as f:
        f.write(_pdf_bytes(RESUME_TEXT))
    conn.execute(
        """
        INSERT INTO documents
            (id, analysis_session_id, document_reference, original_filename, content_version, storage_path,
             size_bytes, content_type, status, idempotency_key, created_at)
        VALUES (%s, %s, 'DOC-001', 'resume.pdf', 1, %s, 100, 'application/pdf', 'ready', 'idem-1', now())
        """,
        (document_id, session_id, storage_path),
    )
    candidate_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO candidates (id, analysis_session_id, document_id, document_reference, created_at)
        VALUES (%s, %s, %s, 'DOC-001', now())
        """,
        (candidate_id, session_id, document_id),
    )
    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO candidate_jobs (id, analysis_revision_id, candidate_id, status, created_at, attempt)
        VALUES (%s, %s, %s, 'queued', now(), %s)
        """,
        (job_id, revision_id, candidate_id, attempt),
    )
    return job_id, candidate_id


def _settings() -> Settings:
    return Settings(
        database_url=WORKER_DATABASE_URL, ollama_host="http://ollama.invalid:11434", azure_openai_configured=False
    )


def test_process_one_candidate_returns_false_when_nothing_queued(conn, tmp_path):
    assert main._process_one_candidate(conn, _settings()) is False


def test_process_one_candidate_success_path_persists_proposal_and_completes(conn, tmp_path, monkeypatch):
    job_id, _ = _seed_pipeline(conn, tmp_path)

    def fake_propose(requirements, source_units, *, base_url, model=None, timeout=60.0):
        return AnalysisProposal(
            items=[
                ProposalItem(
                    job_requirement_id="JR-001",
                    state=AnalysisState.MATCHED,
                    locator=source_units[0].id,
                    excerpt="Python",
                )
            ]
        )

    monkeypatch.setattr(ollama_analysis, "propose", fake_propose)

    claimed = main._process_one_candidate(conn, _settings())
    assert claimed is True

    row = conn.execute("SELECT status FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()
    assert row[0] == "completed"

    proposal_row = conn.execute(
        "SELECT items_json, gate_codes FROM candidate_proposals WHERE candidate_job_id = %s", (job_id,)
    ).fetchone()
    assert proposal_row[0][0]["job_requirement_id"] == "JR-001"
    assert proposal_row[1] == []


def test_process_one_candidate_budget_overflow_stages_no_provider_call(conn, tmp_path, monkeypatch):
    job_id, _ = _seed_pipeline(conn, tmp_path)

    called = {"propose": False}

    def fake_propose(requirements, source_units, *, base_url, model=None, timeout=60.0):
        called["propose"] = True
        raise AssertionError("propose should not be called on budget overflow")

    monkeypatch.setattr(ollama_analysis, "propose", fake_propose)
    monkeypatch.setattr(
        analysis_view,
        "check_budget",
        lambda requirements, permitted_units: analysis_view.BudgetOverflow(
            resume_tokens=99999, resume_budget_tokens=1
        ),
    )

    claimed = main._process_one_candidate(conn, _settings())
    assert claimed is True
    assert called["propose"] is False

    row = conn.execute(
        "SELECT items_json, gate_codes FROM candidate_proposals WHERE candidate_job_id = %s", (job_id,)
    ).fetchone()
    assert row[0] == []
    assert row[1] == ["COVERAGE_BELOW_7000_BPS"]
    status = conn.execute("SELECT status FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "completed"


def test_process_one_candidate_provider_failure_requeues_on_attempt_1(conn, tmp_path, monkeypatch):
    job_id, _ = _seed_pipeline(conn, tmp_path, attempt=1)

    def fake_propose(requirements, source_units, *, base_url, model=None, timeout=60.0):
        raise AnalysisProviderError(FailureReason.TIMEOUT)

    monkeypatch.setattr(ollama_analysis, "propose", fake_propose)

    claimed = main._process_one_candidate(conn, _settings())
    assert claimed is True
    row = conn.execute("SELECT status, attempt FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()
    assert row == ("queued", 2)
    count = conn.execute(
        "SELECT COUNT(*) FROM candidate_proposals WHERE candidate_job_id = %s", (job_id,)
    ).fetchone()[0]
    assert count == 0


def test_process_one_candidate_provider_failure_terminates_after_max_attempts(conn, tmp_path, monkeypatch):
    job_id, _ = _seed_pipeline(conn, tmp_path, attempt=2)

    def fake_propose(requirements, source_units, *, base_url, model=None, timeout=60.0):
        raise AnalysisProviderError(FailureReason.TIMEOUT)

    monkeypatch.setattr(ollama_analysis, "propose", fake_propose)

    claimed = main._process_one_candidate(conn, _settings())
    assert claimed is True
    status = conn.execute("SELECT status FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "failed"


def test_process_one_candidate_unknown_locator_is_staged_as_failure_not_proposal(conn, tmp_path, monkeypatch):
    job_id, _ = _seed_pipeline(conn, tmp_path, attempt=1)

    def fake_propose(requirements, source_units, *, base_url, model=None, timeout=60.0):
        return AnalysisProposal(
            items=[
                ProposalItem(
                    job_requirement_id="JR-001",
                    state=AnalysisState.MATCHED,
                    locator="unit-that-does-not-exist",
                )
            ]
        )

    monkeypatch.setattr(ollama_analysis, "propose", fake_propose)

    claimed = main._process_one_candidate(conn, _settings())
    assert claimed is True

    row = conn.execute("SELECT status, attempt FROM candidate_jobs WHERE id = %s", (job_id,)).fetchone()
    assert row == ("queued", 2)
    count = conn.execute(
        "SELECT COUNT(*) FROM candidate_proposals WHERE candidate_job_id = %s", (job_id,)
    ).fetchone()[0]
    assert count == 0
