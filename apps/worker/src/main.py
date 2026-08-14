"""Worker process entrypoint: boot/healthcheck plus the Job-Requirement-
derivation poll loop (Story 3.5, AD-4) and, since Story 4.4, the complete
Candidate-job attempt (claim -> parse -> protected/proxy-filtered view ->
budget check -> provider call -> fenced staging) — both share the same
AD-6 lease/fencing shape (Story 4.1) and one poll loop, one job claimed at
a time.

The worker's own least-privilege database role (AD-15/AR-43) remains a
future story's scope.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass

import psycopg

from .adapters import candidate_claim, preparation_claim, question_set_claim
from .adapters import resume_parsing
from .adapters import source_unit_serialization
from .adapters.analysis_selection import get_active_adapter
from .config import Settings, load_settings
from .domain import analysis_view
from .domain import identity_extraction
from .domain import quality_provenance
from .domain.analysis_provider import AnalysisProviderError, FailureReason, JobRequirement, validate_locators
from .domain.parse_gates import GateCode, ParseFatalError
from .domain.question_context import build_grounded_context
from .domain.question_provider import validate_question_grounding, validate_question_shape

POLL_INTERVAL_SECONDS = 2
HEARTBEAT_SECONDS = 4


def check_database_connection(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT 1")


def _run_heartbeat(
    database_url: str,
    job_id: str,
    generation: int,
    token: str,
    stop_event: threading.Event,
    heartbeat_fn=preparation_claim.heartbeat,
) -> None:
    """Keeps a claimed job's 12-second lease alive while the main thread
    blocks on a provider call that can take up to 60 seconds (AD-6:
    "processing occurs outside claim transactions"). Runs on its own
    connection — psycopg connections are not safe for concurrent use across
    threads.

    `heartbeat_fn` selects which table's fencing predicate to check —
    `preparation_claim.heartbeat` (default) or `candidate_claim.heartbeat`
    (Story 4.4; its widened predicate already covers both the parse and
    provider phases of one continuous Candidate-job attempt).

    Stops retrying the moment the lease is confirmed lost (fenced out by a
    recovery-sweep reclaim) or the connection itself is unusable — further
    heartbeats would be silently rejected or fail anyway, and the main
    thread's own fenced staging call is what AC#3 ultimately relies on to
    reject a stale write."""
    try:
        conn = psycopg.connect(database_url, autocommit=True)
    except Exception as exc:  # noqa: BLE001 - report and stop, don't crash the process
        print(f"heartbeat thread: could not connect: {exc}", file=sys.stderr)
        return
    try:
        while not stop_event.wait(HEARTBEAT_SECONDS):
            try:
                if not heartbeat_fn(conn, job_id, generation, token):
                    print(
                        f"heartbeat thread: lease for job {job_id} no longer held "
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


@dataclass(frozen=True)
class _CandidateDocument:
    id: str
    content_type: str
    storage_path: str
    content_version: int
    document_reference: str
    original_filename: str


def _fetch_document_for_candidate(conn: psycopg.Connection, candidate_id: str) -> _CandidateDocument:
    """Returns the Document backing this Candidate — a plain
    application-level join, matching this codebase's no-FK-constraints
    convention (candidate_claim tests already document this)."""
    row = conn.execute(
        """
        SELECT d.id, d.content_type, d.storage_path, d.content_version,
               d.document_reference, d.original_filename
        FROM candidates c JOIN documents d ON d.id = c.document_id
        WHERE c.id = %s
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"no candidates/documents join found for candidate {candidate_id}")
    return _CandidateDocument(
        id=str(row[0]),
        content_type=row[1],
        storage_path=row[2],
        content_version=row[3],
        document_reference=row[4],
        original_filename=row[5],
    )


def _fetch_job_requirements(conn: psycopg.Connection, analysis_revision_id: str) -> list[JobRequirement]:
    """Frozen Job Requirements for the Analysis Session behind this Candidate
    job's Revision (join analysis_revisions -> job_requirements). `id` is the
    `display_id` (e.g. "JR-001") — the exact id the provider adapter's user
    message already sends/expects (ollama_analysis._build_user_message)."""
    rows = conn.execute(
        """
        SELECT jr.display_id, jr.canonical_text
        FROM job_requirements jr
        JOIN analysis_revisions ar ON ar.analysis_session_id = jr.analysis_session_id
        WHERE ar.id = %s
        """,
        (analysis_revision_id,),
    ).fetchall()
    return [JobRequirement(id=row[0], text=row[1]) for row in rows]


def _process_one_candidate(conn: psycopg.Connection, settings: Settings) -> bool:
    """Claims and processes at most one queued Candidate job through the
    complete attempt: claim -> read Document bytes -> parse -> stage parse
    checkpoint -> build protected/proxy-filtered view -> budget check ->
    provider call (or budget-overflow proposal) -> stage outcome. Returns
    True if a row was claimed (whether it ultimately succeeded or failed),
    False otherwise."""
    claimed = candidate_claim.claim_queued(conn)
    if claimed is None:
        return False

    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_run_heartbeat,
        args=(settings.database_url, claimed.id, claimed.generation, claimed.token, stop_heartbeat),
        kwargs={"heartbeat_fn": candidate_claim.heartbeat},
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        try:
            document = _fetch_document_for_candidate(conn, claimed.candidate_id)
            with open(document.storage_path, "rb") as f:
                data = f.read()
            units = resume_parsing.parse_resume(data, document.content_type)
            provenance = quality_provenance.build_quality_provenance(units)
            identity = identity_extraction.extract_identity(
                units, document.document_reference, document.original_filename
            )
        except ParseFatalError as exc:
            staged = candidate_claim.stage_parse_failure(
                conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, str(exc)
            )
            if not staged:
                print(
                    f"candidate job {claimed.id}: stage_parse_failure rejected — lease no longer held",
                    file=sys.stderr,
                )
            return True
        except Exception as exc:  # noqa: BLE001 - any unexpected failure before the parse checkpoint
            # must still count against the attempt budget rather than
            # silently rotting until lease expiry + recovery-sweep reclaim.
            print(f"candidate job {claimed.id}: unexpected error before parse checkpoint: {exc}", file=sys.stderr)
            staged = candidate_claim.stage_parse_failure(
                conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, "unexpected processing error"
            )
            if not staged:
                print(
                    f"candidate job {claimed.id}: stage_parse_failure rejected — lease no longer held",
                    file=sys.stderr,
                )
            return True

        parse_staged = candidate_claim.stage_parse_success(
            conn,
            claimed.id,
            claimed.generation,
            claimed.token,
            candidate_id=claimed.candidate_id,
            document_id=document.id,
            document_content_version=document.content_version,
            parser_pipeline_version=resume_parsing.PARSER_PIPELINE_VERSION,
            source_units_json=source_unit_serialization.to_json(units),
            blocks_json=[{"text": b.text, "content_class": b.content_class} for b in provenance.blocks],
            gate_codes=[code.value for code in provenance.gate_codes],
            coherent_block_count=provenance.coherent_block_count,
            display_name=identity.display_name,
            name_source=identity.name_source,
            email=identity.email,
            phone=identity.phone,
        )
        if not parse_staged:
            # AC#3: lease already lost (sweep reclaim) — the parse write was
            # correctly rejected; do not proceed to a provider call under a
            # fence already known stale.
            print(
                f"candidate job {claimed.id}: stage_parse_success rejected — lease no longer held",
                file=sys.stderr,
            )
            return True

        requirements = _fetch_job_requirements(conn, claimed.analysis_revision_id)
        permitted_units = analysis_view.build_analysis_view(units)
        overflow = analysis_view.check_budget(requirements, permitted_units)
        if overflow is not None:
            staged = candidate_claim.stage_provider_success(
                conn,
                claimed.id,
                claimed.generation,
                claimed.token,
                candidate_id=claimed.candidate_id,
                analysis_revision_id=claimed.analysis_revision_id,
                items_json=[],
                gate_codes=[GateCode.COVERAGE_BELOW_7000_BPS.value],
            )
        else:
            try:
                adapter = get_active_adapter(settings)
                proposal = adapter.propose(requirements, permitted_units, base_url=settings.ollama_host)
                validate_locators(proposal, permitted_units)
            except AnalysisProviderError as exc:
                staged = candidate_claim.stage_provider_failure(
                    conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, exc.category
                )
                if not staged:
                    print(
                        f"candidate job {claimed.id}: stage_provider_failure rejected — lease no longer held",
                        file=sys.stderr,
                    )
                return True
            except ValueError as exc:
                # Locally-detected unknown locator — same sanitized failure
                # vocabulary/staging path as an adapter-raised failure, not a
                # second one (AC#2).
                mapped = AnalysisProviderError(FailureReason.MALFORMED)
                staged = candidate_claim.stage_provider_failure(
                    conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, mapped.category
                )
                if not staged:
                    print(
                        f"candidate job {claimed.id}: stage_provider_failure rejected — lease no longer held",
                        file=sys.stderr,
                    )
                return True
            except Exception as exc:  # noqa: BLE001 - an unclassified failure must still count
                # against the attempt budget, not silently rot until lease
                # expiry + recovery-sweep reclaim.
                print(f"candidate job {claimed.id}: unexpected error during provider phase: {exc}", file=sys.stderr)
                mapped = AnalysisProviderError(FailureReason.INTERRUPTED)
                staged = candidate_claim.stage_provider_failure(
                    conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, mapped.category
                )
                if not staged:
                    print(
                        f"candidate job {claimed.id}: stage_provider_failure rejected — lease no longer held",
                        file=sys.stderr,
                    )
                return True
            staged = candidate_claim.stage_provider_success(
                conn,
                claimed.id,
                claimed.generation,
                claimed.token,
                candidate_id=claimed.candidate_id,
                analysis_revision_id=claimed.analysis_revision_id,
                items_json=[item.model_dump() for item in proposal.items],
                gate_codes=[],
            )
        if not staged:
            print(
                f"candidate job {claimed.id}: stage_provider_success rejected — lease no longer held",
                file=sys.stderr,
            )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=HEARTBEAT_SECONDS * 2)
        if heartbeat_thread.is_alive():
            print(
                f"candidate job {claimed.id}: heartbeat thread did not stop in time (leaked, daemon)",
                file=sys.stderr,
            )
    return True


def _process_one_question_set(conn: psycopg.Connection, settings: Settings) -> bool:
    """Claims and processes at most one queued Question Set job (Story 7.1):
    claim -> read grounded context (already-persisted Job Requirements +
    the Candidate's existing Evidence proposal + source-unit locators, no
    parse phase needed) -> provider call -> stage fenced proposal or
    failure. Returns True if a row was claimed (whether it ultimately
    succeeded or failed), False otherwise."""
    claimed = question_set_claim.claim_queued(conn)
    if claimed is None:
        return False

    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_run_heartbeat,
        args=(settings.database_url, claimed.id, claimed.generation, claimed.token, stop_heartbeat),
        kwargs={"heartbeat_fn": question_set_claim.heartbeat},
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        # Code review fix (Blind Hunter/Edge Case Hunter, High): the fetch/
        # build phase previously ran with no except at all — a malformed
        # historical proposal item, a missing requirement, or any DB hiccup
        # propagated uncaught to `_poll_loop`'s catch-all, leaving the row
        # `claimed` with a live lease and no attempt consumed (compounded by
        # the also-fixed missing recovery-sweep coverage). Mirrors
        # `_process_one_candidate`'s identical pre-provider-checkpoint
        # guard (`except Exception` around parsing, staging a failure that
        # counts against the attempt budget rather than silently rotting).
        try:
            requirement_texts, proposal_items, unit_locators = question_set_claim.fetch_grounded_data(
                conn, claimed.candidate_id, claimed.analysis_revision_id
            )
            grounded = build_grounded_context(proposal_items, requirement_texts, unit_locators)
        except Exception as exc:  # noqa: BLE001 - must still count against the attempt budget
            print(f"question set job {claimed.id}: unexpected error building grounded context: {exc}", file=sys.stderr)
            mapped = AnalysisProviderError(FailureReason.INTERRUPTED)
            staged = question_set_claim.stage_failure(
                conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, mapped.category
            )
            if not staged:
                print(
                    f"question set job {claimed.id}: stage_failure rejected — lease no longer held",
                    file=sys.stderr,
                )
            return True

        if not grounded:
            # Code review fix (all 3 layers, High): a Candidate with no
            # persisted Evidence proposal (or an empty one, e.g. the
            # COVERAGE_BELOW_7000_BPS budget-overflow staging path) must
            # never reach the provider — ten "grounded" questions with zero
            # actual grounding is exactly the fabrication NFR-1 forbids.
            mapped = AnalysisProviderError(FailureReason.MALFORMED)
            staged = question_set_claim.stage_failure(
                conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, mapped.category
            )
            if not staged:
                print(
                    f"question set job {claimed.id}: stage_failure rejected — lease no longer held",
                    file=sys.stderr,
                )
            return True

        try:
            adapter = get_active_adapter(settings)
            proposal = adapter.propose_questions(grounded, base_url=settings.ollama_host)
            validate_question_shape(proposal)
            validate_question_grounding(proposal, grounded)
        except AnalysisProviderError as exc:
            staged = question_set_claim.stage_failure(
                conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, exc.category
            )
            if not staged:
                print(
                    f"question set job {claimed.id}: stage_failure rejected — lease no longer held",
                    file=sys.stderr,
                )
            return True
        except ValueError as exc:
            # Locally-detected malformed shape (validate_question_shape) —
            # same sanitized failure vocabulary/staging path as an
            # adapter-raised failure, not a second one.
            mapped = AnalysisProviderError(FailureReason.MALFORMED)
            staged = question_set_claim.stage_failure(
                conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, mapped.category
            )
            if not staged:
                print(
                    f"question set job {claimed.id}: stage_failure rejected — lease no longer held",
                    file=sys.stderr,
                )
            return True
        except Exception as exc:  # noqa: BLE001 - an unclassified failure must still count
            # against the attempt budget, not silently rot until lease
            # expiry + recovery-sweep reclaim.
            print(f"question set job {claimed.id}: unexpected error during provider phase: {exc}", file=sys.stderr)
            mapped = AnalysisProviderError(FailureReason.INTERRUPTED)
            staged = question_set_claim.stage_failure(
                conn, claimed.id, claimed.attempt, claimed.generation, claimed.token, mapped.category
            )
            if not staged:
                print(
                    f"question set job {claimed.id}: stage_failure rejected — lease no longer held",
                    file=sys.stderr,
                )
            return True

        staged = question_set_claim.stage_success(
            conn,
            claimed.id,
            claimed.generation,
            claimed.token,
            candidate_id=claimed.candidate_id,
            analysis_revision_id=claimed.analysis_revision_id,
            items_json=[item.model_dump() for item in proposal.items],
        )
        if not staged:
            print(
                f"question set job {claimed.id}: stage_success rejected — lease no longer held",
                file=sys.stderr,
            )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=HEARTBEAT_SECONDS * 2)
        if heartbeat_thread.is_alive():
            print(
                f"question set job {claimed.id}: heartbeat thread did not stop in time (leaked, daemon)",
                file=sys.stderr,
            )
    return True


def _poll_loop(settings: Settings) -> None:
    # ponytail: single-threaded poll loop, one job at a time — a dedicated
    # worker pool/queue depth is a scaling concern for later stories, not
    # V1's single-demo-session scale. Story 4.4/7.1: only try the next queue
    # (Candidate jobs, then Question Set jobs) this tick if every earlier
    # queue claimed nothing — each tick claims at most one row total either
    # way, so over successive ticks no queue starves the others at V1's
    # small demo scale.
    conn = psycopg.connect(settings.database_url, autocommit=True)
    try:
        while True:
            try:
                if not _process_one(conn, settings):
                    if not _process_one_candidate(conn, settings):
                        _process_one_question_set(conn, settings)
            except Exception as exc:  # noqa: BLE001 - one bad iteration must not kill the loop
                print(f"worker claim loop iteration failed: {exc}", file=sys.stderr)
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        conn.close()


def main() -> None:
    settings = load_settings()
    check_database_connection(settings.database_url)
    print("worker: database connection OK, polling for preparations and Candidate jobs", file=sys.stderr)
    _poll_loop(settings)


if __name__ == "__main__":
    main()
