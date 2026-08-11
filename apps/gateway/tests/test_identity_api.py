"""AC#3, #4, #5: session cookie contract and neutral fail-closed responses at
the FastAPI boundary. Skips at collection time (not just fixture time) since
importing the app requires a live DATABASE_URL (AC#5 fail-fast pattern).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient

from src.adapters.api import app

client = TestClient(app)


def _email() -> str:
    return f"user-{uuid.uuid4()}@example.com"


def test_register_login_session_logout_round_trip(db_session):
    email = _email()
    password = "correct horse battery staple"

    register = client.post("/identity/register", json={"email": email, "password": password})
    assert register.status_code == 201

    login = client.post("/identity/login", json={"email": email, "password": password})
    assert login.status_code == 200
    cookie = login.cookies.get("session")
    assert cookie

    # session cookie flags: HttpOnly + Secure + SameSite=Lax (never browser-JS-visible)
    set_cookie_header = login.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_header
    assert "Secure" in set_cookie_header
    assert "samesite=lax" in set_cookie_header.lower()

    client.cookies.set("session", cookie)
    whoami = client.get("/identity/session")
    # not yet admitted -> neutral 403, not a leak of "which" condition
    assert whoami.status_code == 403

    logout = client.post("/identity/logout")
    assert logout.status_code == 200
    client.cookies.clear()


def test_duplicate_registration_is_neutral(db_session):
    email = _email()
    password = "a fixture password"
    first = client.post("/identity/register", json={"email": email, "password": password})
    assert first.status_code == 201
    second = client.post("/identity/register", json={"email": email, "password": password})
    assert second.status_code == 409


def test_invalid_credentials_and_missing_session_are_neutral(db_session):
    bad_login = client.post(
        "/identity/login", json={"email": _email(), "password": "does-not-matter"}
    )
    assert bad_login.status_code == 401

    client.cookies.clear()
    no_session = client.get("/identity/session")
    assert no_session.status_code == 401

    client.cookies.set("session", "tampered-or-unknown-token")
    tampered = client.get("/identity/session")
    assert tampered.status_code == 401
    client.cookies.clear()


def test_admitted_identity_reaches_protected_route(db_session):
    from src.adapters import local_identity

    email = _email()
    password = "a fixture password for admission"
    local_identity.register(db_session, email, password)

    login = client.post("/identity/login", json={"email": email, "password": password})
    cookie = login.cookies.get("session")

    session = local_identity.validate_session(db_session, cookie)
    local_identity.admit_user(db_session, session.subject)

    client.cookies.set("session", cookie)
    whoami = client.get("/identity/session")
    assert whoami.status_code == 200
    body = whoami.json()
    # AC#1: normalizes only (issuer, subject) — never email, display name, or
    # Organization membership, which the response body has no field for.
    assert body == {"issuer": "local", "subject": session.subject}
    assert set(body.keys()) == {"issuer", "subject"}
    client.cookies.clear()


def test_expired_session_fails_closed_at_the_api_boundary(db_session):
    """AC#2: an expired session must fail closed through the actual FastAPI
    route, not just at local_identity.validate_session's unit level
    (already covered by test_local_identity.py::test_fail_closed_session_validation).
    Uses an already-admitted principal so this proves expiry overrides an
    otherwise-successful 200 path, not just a not-yet-admitted 403 path.
    """
    from src.adapters import local_identity
    from src.adapters.models import Session as SessionRecord

    email = _email()
    password = "a fixture password for expiry"
    local_identity.register(db_session, email, password)
    login = client.post("/identity/login", json={"email": email, "password": password})
    cookie = login.cookies.get("session")
    assert cookie

    session = local_identity.validate_session(db_session, cookie)
    assert session is not None
    local_identity.admit_user(db_session, session.subject)
    client.cookies.set("session", cookie)
    assert client.get("/identity/session").status_code == 200

    # The test's db_session and the app's request-scoped session both point
    # at the same live DATABASE_URL, so this commit is visible to the next
    # request under Postgres's default read-committed isolation.
    record = db_session.get(SessionRecord, cookie)
    assert record is not None
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    expired = client.get("/identity/session")
    assert expired.status_code == 401
    assert expired.json() == {"detail": "Authentication failed"}
    client.cookies.clear()


def test_revoked_session_fails_closed_at_the_api_boundary(db_session):
    """AC#2: reusing a logged-out cookie must fail closed, same neutral
    shape as any other authentication failure — no distinguishing detail.
    Uses an already-admitted principal so this proves revocation overrides
    an otherwise-successful 200 path, not just a not-yet-admitted 403 path.
    """
    from src.adapters import local_identity

    email = _email()
    password = "a fixture password for revocation"
    local_identity.register(db_session, email, password)
    login = client.post("/identity/login", json={"email": email, "password": password})
    cookie = login.cookies.get("session")
    assert cookie

    session = local_identity.validate_session(db_session, cookie)
    assert session is not None
    local_identity.admit_user(db_session, session.subject)
    client.cookies.set("session", cookie)
    assert client.get("/identity/session").status_code == 200

    client.post("/identity/logout")

    reused = client.get("/identity/session")
    assert reused.status_code == 401
    assert reused.json() == {"detail": "Authentication failed"}
    client.cookies.clear()


def test_not_admitted_and_not_authenticated_stay_distinct_neutral_families(db_session):
    """AC#1/#2: admission (403) and authentication (401) are two separate
    fail-closed checks — neither leaks into or collapses with the other."""
    from src.adapters import local_identity

    email = _email()
    password = "a fixture password for family check"
    local_identity.register(db_session, email, password)
    login = client.post("/identity/login", json={"email": email, "password": password})
    cookie = login.cookies.get("session")
    assert cookie

    client.cookies.set("session", cookie)
    not_admitted = client.get("/identity/session")
    assert not_admitted.status_code == 403
    assert not_admitted.json() == {"detail": "Access not available"}
    client.cookies.clear()

    client.cookies.set("session", "tampered-or-unknown-token")
    not_authenticated = client.get("/identity/session")
    assert not_authenticated.status_code == 401
    assert not_authenticated.json() == {"detail": "Authentication failed"}
    client.cookies.clear()


def test_malformed_and_repeated_requests_create_no_state(db_session):
    """AC#2: 'malformed and repeated requests create no state' — a flood of
    tampered-token reads must not write a new row to the sessions table on
    the way to rejecting them (the only stateful table this fail-closed path
    can reach; no login-attempt/audit table exists yet)."""
    from sqlalchemy import func, select

    from src.adapters.models import Session as SessionRecord

    before = db_session.execute(select(func.count()).select_from(SessionRecord)).scalar_one()

    client.cookies.set("session", "tampered-token-one")
    for _ in range(5):
        response = client.get("/identity/session")
        assert response.status_code == 401
    client.cookies.clear()

    after = db_session.execute(select(func.count()).select_from(SessionRecord)).scalar_one()
    assert after == before


def test_repeated_valid_reads_are_idempotent(db_session):
    """AC#2: reads are non-mutating; repeated checks are idempotent — two
    consecutive valid requests return byte-identical bodies, and the
    session row itself (not just the table's row count) is untouched, e.g.
    a sliding-expiry side effect would fail this."""
    from sqlalchemy import func, select

    from src.adapters import local_identity
    from src.adapters.models import Session as SessionRecord

    email = _email()
    password = "a fixture password for idempotent reads"
    local_identity.register(db_session, email, password)
    login = client.post("/identity/login", json={"email": email, "password": password})
    cookie = login.cookies.get("session")
    assert cookie

    session = local_identity.validate_session(db_session, cookie)
    assert session is not None
    local_identity.admit_user(db_session, session.subject)

    client.cookies.set("session", cookie)
    before_count = db_session.execute(select(func.count()).select_from(SessionRecord)).scalar_one()
    db_session.expire_all()
    before_record = db_session.get(SessionRecord, cookie)
    before_expires_at, before_revoked_at = before_record.expires_at, before_record.revoked_at

    first = client.get("/identity/session")
    second = client.get("/identity/session")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()

    after_count = db_session.execute(select(func.count()).select_from(SessionRecord)).scalar_one()
    assert after_count == before_count
    db_session.expire_all()
    after_record = db_session.get(SessionRecord, cookie)
    assert (after_record.expires_at, after_record.revoked_at) == (before_expires_at, before_revoked_at)
    client.cookies.clear()
