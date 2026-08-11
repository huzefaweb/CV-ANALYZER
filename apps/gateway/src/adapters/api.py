"""FastAPI application entrypoint for the gateway process."""

from __future__ import annotations

import sys

from fastapi import FastAPI

from .config import load_settings
from .db import init_engine
from .document_upload import router as document_upload_router
from .identity import router as identity_router
from .new_analysis import router as new_analysis_router
from .workspace import router as workspace_router

# Validated once at process startup (AC#5): a missing secret must fail the
# boot, not surface as a SystemExit thrown from inside a request handler.
settings = load_settings()
init_engine(settings.database_url)

app = FastAPI(title="CV Analyzer Gateway")

# workspace_router and new_analysis_router have no adapter-specific routes —
# mounted unconditionally, unlike identity_router. They still only admit
# requests through require_admitted_identity's current (local-only; see
# Story 2.2 Scope reality check) admission chain, same as every other
# protected route today.
app.include_router(workspace_router)
app.include_router(new_analysis_router)
app.include_router(document_upload_router)

# AD-21: only the active adapter's routes are mounted. AUTH0_* absent (V1
# default) selects the local adapter; the Auth0 adapter preflight is
# deferred (AC#7) until those vars are configured — no routes to mount yet.
if not settings.auth0_configured:
    app.include_router(identity_router)
else:
    # AC#7: Auth0 preflight is deferred, not implemented — no adapter is
    # live yet, so boot proceeds with zero identity routes rather than
    # failing closed silently. Loud on purpose: this is not the expected
    # V1 state, so an operator who sets AUTH0_* vars should know why login
    # stopped working instead of discovering it via a 404.
    print(
        "AUTH0_* env vars are set, but the Auth0 adapter preflight (AC#7) is "
        "deferred and unimplemented — no identity routes are mounted.",
        file=sys.stderr,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
