"""FastAPI application entrypoint for the gateway process."""

from __future__ import annotations

from fastapi import FastAPI

from .config import load_settings

# Validated once at process startup (AC#5): a missing secret must fail the
# boot, not surface as a SystemExit thrown from inside a request handler.
load_settings()

app = FastAPI(title="CV Analyzer Gateway")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
