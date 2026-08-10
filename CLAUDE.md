# CLAUDE.md

Guidance for Claude Code sessions working in this repository — both interactive and the ones bmad-loop spawns unattended.

## What this project is

CV-ANALYZER: a private, single-Organization recruiter tool that scores and ranks candidate Resumes against a Job Description, with full Evidence traceability and human-controlled decisions (see `_bmad-output/planning-artifacts/prds/prd-CV-ANALYZER-2026-08-09/prd.md`). V1 scope is one prepared 12-hour private demo — not a production system. Solo owner: Huzefa is both Engineering Owner and Product Owner.

## Where things live

- **PRD:** `_bmad-output/planning-artifacts/prds/prd-CV-ANALYZER-2026-08-09/prd.md`
- **Architecture:** `_bmad-output/planning-artifacts/architecture/architecture-CV-ANALYZER-2026-08-09/` (`ARCHITECTURE-SPINE.md`, `SOLUTION-DESIGN.md`, `DECISIONS.md`, `MODELS.md`)
- **UX:** `_bmad-output/planning-artifacts/ux-designs/ux-CV-ANALYZER-2026-08-09/` (`DESIGN.md`, `EXPERIENCE.md`, mockups/)
- **Epics & stories (source of truth for scope/ACs):** `_bmad-output/planning-artifacts/epics.md` — 8 epics, 41 stories
- **Sprint tracking:** `_bmad-output/implementation-artifacts/sprint-status.yaml`
- **BMAD/AI agent rules:** `_bmad-output/project-context.md` — loaded automatically by every BMAD skill (sprint planning, dev-auto, etc.); read it too, it has rules not repeated here.
- **Readiness gate:** `_bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md` — PASS WITH CAVEAT; Epic 1 (Stories 1.1–1.7) must still run for real before the implementation day.

`_bmad-output/` and `graphify-out/` are gitignored by choice — they exist on disk but are not committed. Don't infer "doesn't exist" from `git status`; check the filesystem.

## Architecture invariants (don't relitigate these)

- Modular Web–Queue–Worker: Next.js ↔ FastAPI gateway ↔ Python analyzer worker ↔ PostgreSQL. One host, one Compose profile.
- No Redis, Celery, event bus, GraphQL, WebSockets, service mesh, or vector store in V1 — deliberate, not an oversight.
- Domain/scoring/Evidence/retry/publication logic is framework-free (no DB driver, web framework, identity SDK, parser, or provider SDK imports).
- Scoring is exact rational arithmetic (PostgreSQL `NUMERIC`), never floating point.
- Authorization is fail-closed and re-checked independently at every descendant read/mutation — never trust a parent check already ran.
- Full requirement and terminology list: `epics.md` "Requirements Inventory" and "Taxonomy" sections. Terms like Analysis Session, Analysis Revision, Not Found vs. Needs Validation, Failed vs. Needs Review are exact contract terms — preserve them verbatim in code, UI copy, and commits.

## How this repo works with AI agents

- **Ponytail** (lazy-but-correct coding discipline) is enabled project-wide via `.claude/settings.local.json` and applies automatically every session — no need to re-invoke it, just actually follow the ladder: real need → reuse existing code → stdlib/native → existing dependency → minimal new code.
- **Graphify** — a local, free (tree-sitter, no LLM) knowledge graph of this codebase lives in `graphify-out/`. Prefer `graphify query "<question>"` / `graphify explain <node>` / `graphify path A B` over cold grep-and-read sweeps when exploring "where is X" / "what calls Y". Rebuild with `/graphify .` if it's missing or stale for the area you're touching.
- **No external orchestrator.** The per-story dev loop runs natively inside this Claude Code session — not through the `bmad-loop` CLI/tmux orchestrator (deliberately dropped: it spawned untrackable sessions with no visibility into whether ponytail/graphify were actually followed). Story queue source is still `_bmad-output/implementation-artifacts/sprint-status.yaml`.

### Per-story loop (say "loop story `<epic>.<story>`", e.g. "loop story 1.2")

Run these in order, in this same session, so ponytail/graphify usage is directly visible rather than delegated to a spawned process:

1. **CS — `bmad-create-story`** for the given story key. Produces `_bmad-output/implementation-artifacts/spec-<key>-<slug>.md`.
2. **Validate + fix** — check the produced spec against the Ready-for-Development standard (Actionable, Logical, Testable, Complete, Sufficient, Coherent — see `bmad-dev-auto`'s definition). Fix gaps directly in the spec before moving on; don't hand a broken spec to dev.
3. **DS — `bmad-dev-story`** on that spec file. Runs to completion (all ACs + tasks checked) or a HALT condition.
4. **CR — `bmad-code-review`** on the resulting diff. Apply the findings (fix, or explicitly mark skipped with reason).
5. **Commit** referencing the story's FR/NFR/AR/AD/UX-DR traceability IDs, then flip that story's `sprint-status.yaml` entry to `done`.

Pick up the next story only when explicitly told the next number — this is intentionally one-story-at-a-time, not an unattended sweep through all 41.

## Non-negotiables from the PRD (recur across almost every story)

- No provisional/partial ranking ever shown.
- No recruiter-editable scoring weights, no automatic reject/shortlist/advance behavior — score/rank never decides for the Recruiter.
- Candidate identity/contact is stored for authorized display only, never sent to the AI provider and never used in scoring.
- Every favorable/adverse claim must resolve to a Job Requirement + verifiable Resume Evidence; unverifiable claims score zero.
