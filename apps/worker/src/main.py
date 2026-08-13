"""Worker process entrypoint: boot/healthcheck plus the Job-Requirement-
derivation poll loop (Story 3.5, AD-4), now with full AD-6 lease/fencing
(Story 4.1).

Claim/lease/analysis logic for Candidate analysis (AD-6) and the worker's
own least-privilege role (AD-15/AR-43) remain future stories' scope — this
loop only drives `start_preparations` through the leased claim cycle
`preparation_claim.py` provides.
"""

from __future__ import annotations

import sys
import threading
import time

import psycopg

from .adapters import preparation_claim
from .adapters.analysis_selection import get_active_adapter
from .config import Settings, load_settings
from .domain.requirement_derivation import AnalysisProviderError

POLL_INTERVAL_SECONDS = 2
HEARTBEAT_SECONDS = 4


def check_database_connection(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT 1")


def _run_heartbeat(
    database_url: str, preparation_id: str, generation: int, token: str, stop_event: threading.Event
) -> None:
    """Keeps a claimed preparation's 12-second lease alive while the main
    thread blocks on a provider call that can take up to 60 seconds (AD-6:
    "processing occurs outside claim transactions"). Runs on its own
    connection — psycopg connections are not safe for concurrent use across
    threads.

    Stops retrying the moment the lease is confirmed lost (fenced out by a
    recovery-sweep reclaim) or the connection itself is unusable — further
    heartbeats would be silently rejected or fail anyway, and the main
    thread's own fenced `stage_success`/`stage_failure` call is what AC#3
    ultimately relies on to reject a stale write."""
    try:
        conn = psycopg.connect(database_url, autocommit=True)
    except Exception as exc:  # noqa: BLE001 - report and stop, don't crash the process
        print(f"heartbeat thread: could not connect: {exc}", file=sys.stderr)
        return
    try:
        while not stop_event.wait(HEARTBEAT_SECONDS):
            try:
                if not preparation_claim.heartbeat(conn, preparation_id, generation, token):
                    print(
                        f"heartbeat thread: lease for preparation {preparation_id} no longer held "
                        "(reclaimed or expired) — stopping heartbeats",
                        file=sys.stderr,
                    )
                    return
            except Exception as exc:  # noqa: BLE001 - one failed heartbeat must not kill the thread
                print(f"heartbeat thread: heartbeat call failed, will retry: {exc}", file=sys.stderr)
    finally:
        conn.close()


def _process_one(conn: psycopg.Connection, settings: Settings) -> bool:
    """Claims and processes at most one queued preparation. Returns True if
    a row was claimed (whether it succeeded or failed), False otherwise."""
    claimed = preparation_claim.claim_queued(conn)
    if claimed is None:
        return False

    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_run_heartbeat,
        args=(settings.database_url, claimed.id, claimed.generation, claimed.token, stop_heartbeat),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        job_description_text = preparation_claim.fetch_job_description_text(
            conn, claimed.analysis_session_id
        )
        adapter = get_active_adapter(settings)
        proposal = adapter.derive_requirements(job_description_text, base_url=settings.ollama_host)
        staged = preparation_claim.stage_success(
            conn, claimed.id, claimed.generation, claimed.token, proposal.model_dump()
        )
        if not staged:
            # AC#3: the lease was reclaimed (sweep) or lost before this write
            # — the write was correctly rejected (no state changed), but
            # this is worth surfacing: the provider call ran for nothing.
            print(
                f"preparation {claimed.id}: stage_success rejected — lease no longer held",
                file=sys.stderr,
            )
    except AnalysisProviderError as exc:
        staged = preparation_claim.stage_failure(
            conn,
            claimed.id,
            claimed.analysis_session_id,
            claimed.attempt,
            claimed.generation,
            claimed.token,
            exc.category,
        )
        if not staged:
            print(
                f"preparation {claimed.id}: stage_failure rejected — lease no longer held",
                file=sys.stderr,
            )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=HEARTBEAT_SECONDS * 2)
        if heartbeat_thread.is_alive():
            print(
                f"preparation {claimed.id}: heartbeat thread did not stop in time (leaked, daemon)",
                file=sys.stderr,
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
