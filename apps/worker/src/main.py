"""Worker process entrypoint: boot/healthcheck plus the Job-Requirement-
derivation poll loop (Story 3.5, AD-4).

Claim/lease/analysis logic for Candidate analysis (AD-6) and the worker's
own least-privilege role (AD-15/AR-43) remain future stories' scope — this
loop only drives `start_preparations` through the minimal-CAS claim cycle
`preparation_claim.py` provides (see that module's docstring for why this
is deliberately not the full AD-6 lease/fencing protocol).
"""

from __future__ import annotations

import sys
import time

import psycopg

from .adapters import preparation_claim
from .adapters.analysis_selection import get_active_adapter
from .config import Settings, load_settings
from .domain.requirement_derivation import AnalysisProviderError

POLL_INTERVAL_SECONDS = 2


def check_database_connection(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT 1")


def _process_one(conn: psycopg.Connection, settings: Settings) -> bool:
    """Claims and processes at most one queued preparation. Returns True if
    a row was claimed (whether it succeeded or failed), False otherwise."""
    claimed = preparation_claim.claim_queued(conn)
    if claimed is None:
        return False

    try:
        job_description_text = preparation_claim.fetch_job_description_text(
            conn, claimed.analysis_session_id
        )
        adapter = get_active_adapter(settings)
        proposal = adapter.derive_requirements(job_description_text, base_url=settings.ollama_host)
        preparation_claim.stage_success(conn, claimed.id, proposal.model_dump())
    except AnalysisProviderError as exc:
        preparation_claim.stage_failure(
            conn, claimed.id, claimed.analysis_session_id, claimed.attempt, exc.category
        )
    return True


def _poll_loop(settings: Settings) -> None:
    # ponytail: single-threaded poll loop, one preparation at a time — a
    # dedicated worker pool/queue depth is a scaling concern for later
    # stories, not V1's single-demo-session scale.
    conn = psycopg.connect(settings.database_url, autocommit=True)
    try:
        while True:
            try:
                _process_one(conn, settings)
            except Exception as exc:  # noqa: BLE001 - one bad iteration must not kill the loop
                print(f"preparation claim loop iteration failed: {exc}", file=sys.stderr)
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        conn.close()


def main() -> None:
    settings = load_settings()
    check_database_connection(settings.database_url)
    print("worker: database connection OK, polling for preparations", file=sys.stderr)
    _poll_loop(settings)


if __name__ == "__main__":
    main()
