# CV-ANALYZER
CV Analyzer is a web application that helps HR teams compare multiple candidate resumes against a specific job description. The application will automatically evaluate candidate fit, rank resumes from strongest to weakest match, explain the reasoning behind each ranking, and generate tailored interview questions for shortlisted candidates.

## Bootstrap (local V1 scaffold)

**Prerequisites:** Docker and Docker Compose. Nothing else is required locally — Next.js, FastAPI, the Python worker, and PostgreSQL all run inside Compose.

```sh
cp .env.example .env   # fill in POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
docker compose up
```

This starts, on one Compose profile: the Next.js web process (`:3000`), the FastAPI gateway (`:8000`, runs Alembic migrations on boot), the Python analyzer worker, and PostgreSQL (`:5432`). Rerunning `docker compose up` against the same volume re-applies migrations idempotently and does not duplicate or corrupt migration state.

Exact dependency versions are pinned in each app's lockfile (`apps/web/package-lock.json`, `apps/gateway/uv.lock`, `apps/worker/uv.lock`) and the container base images/PostgreSQL are pinned by digest in the `Dockerfile`s and `docker-compose.yml`. Changing any of these pins requires rerunning the affected AF-13 evidence rather than accepting silent drift — see `_bmad-output/implementation-artifacts/af13-scaffold-evidence.md`.

If any scaffold, dependency, migration, health, or boundary check fails, the AF-13 scaffold item remains unverified and the 12-hour implementation clock must not begin.
