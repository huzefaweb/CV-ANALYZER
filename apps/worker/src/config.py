"""Startup configuration loading with sanitized diagnostics (AC#5).

Missing required environment variables are reported by name only — never by
value — and cause a non-zero exit so the process fails fast instead of
running with an invalid configuration.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

REQUIRED_ENV_VARS = ("WORKER_DATABASE_URL",)

# Presence of all four (AD-22) selects the Azure OpenAI adapter instead of
# Ollama; they are never required — their absence is the expected
# V1-default state. Mirrors apps/gateway/src/adapters/config.py's
# AUTH0_ENV_VARS pattern from Story 1.2.
AZURE_OPENAI_ENV_VARS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
)

DEFAULT_OLLAMA_HOST = "http://ollama:11434"
# Matches the pin recorded in af13-analysis-provider-evidence.md — the one
# model this host is guaranteed to already have pulled. OLLAMA_MODEL in
# .env overrides it (e.g. to a larger qwen2.5 tier on stronger hardware);
# the override is not itself pulled automatically — `ollama pull` for it
# first, same as the default's own documented pre-demo step.
DEFAULT_OLLAMA_MODEL = "qwen2.5:0.5b-instruct"


@dataclass(frozen=True)
class Settings:
    database_url: str
    ollama_host: str
    ollama_model: str
    azure_openai_configured: bool


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = env if env is not None else os.environ
    missing = [name for name in REQUIRED_ENV_VARS if not source.get(name)]
    if missing:
        for name in missing:
            print(f"Missing required environment variable: {name}", file=sys.stderr)
        raise SystemExit(1)
    azure_openai_configured = all((source.get(name) or "").strip() for name in AZURE_OPENAI_ENV_VARS)
    return Settings(
        database_url=source["WORKER_DATABASE_URL"],
        ollama_host=source.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST,
        ollama_model=source.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
        azure_openai_configured=azure_openai_configured,
    )
