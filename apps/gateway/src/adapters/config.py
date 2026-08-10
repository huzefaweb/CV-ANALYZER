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


@dataclass(frozen=True)
class Settings:
    database_url: str


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = env if env is not None else os.environ
    missing = [name for name in REQUIRED_ENV_VARS if not source.get(name)]
    if missing:
        for name in missing:
            print(f"Missing required environment variable: {name}", file=sys.stderr)
        raise SystemExit(1)
    return Settings(database_url=source["DATABASE_URL"])
