"""Worker boot-and-healthcheck stub.

This story only proves the worker process can start and reach PostgreSQL.
Claim/lease/analysis logic (AD-6) and the worker's own least-privilege
role (AD-15/AR-43) arrive with the story that creates the first table
to scope grants against.
"""

from __future__ import annotations

import sys
import time

import psycopg

from .config import load_settings


def check_database_connection(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT 1")


def main() -> None:
    settings = load_settings()
    check_database_connection(settings.database_url)
    print("worker: database connection OK, idling", file=sys.stderr)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
