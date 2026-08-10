"""Startup configuration loading with sanitized diagnostics (AC#5).

Missing required environment variables are reported by name only — never by
value — and cause a non-zero exit so the process fails fast instead of
serving with an invalid configuration.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

REQUIRED_ENV_VARS = ("DATABASE_URL",)

# Presence of these (AD-21) selects the Auth0 adapter instead of local; they
# are never required — their absence is the expected V1-default state.
AUTH0_ENV_VARS = ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET")


@dataclass(frozen=True)
class Settings:
    database_url: str
    auth0_configured: bool


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = env if env is not None else os.environ
    missing = [name for name in REQUIRED_ENV_VARS if not source.get(name)]
    if missing:
        for name in missing:
            print(f"Missing required environment variable: {name}", file=sys.stderr)
        raise SystemExit(1)
    auth0_configured = all(source.get(name) for name in AUTH0_ENV_VARS)
    return Settings(database_url=source["DATABASE_URL"], auth0_configured=auth0_configured)
