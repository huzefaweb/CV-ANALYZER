-- Story 1.5a: harness-only lease/fencing proof schema (AD-6, AR-14, AR-16).
--
-- This is NOT the production analysis-job table -- Story 4.1 ("Claim Durable
-- Work with Leases and Fencing") owns that as a real apps/gateway alembic
-- migration. This table exists only so this story's pytest fixtures can
-- prove the claim/fence/heartbeat/reclaim/finalize mechanism against real
-- PostgreSQL before Story 4.1 builds on top of it.
--
-- job_pk is the surrogate primary key -- one row per attempt. logical_id is
-- the stable business identity a fixture represents and is unique only
-- *per attempt*, because an exhausted attempt 1 row and its attempt 2
-- successor intentionally share the same logical_id.
CREATE TABLE IF NOT EXISTS job_lease_proof (
    job_pk UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    logical_id TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    token TEXT,
    state_version INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    attempt INTEGER NOT NULL DEFAULT 1,
    reclaim_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (logical_id, attempt)
);
