"""AC#3, #4, #5: session cookie contract and neutral fail-closed responses at
the FastAPI boundary. Skips at collection time (not just fixture time) since
importing the app requires a live DATABASE_URL (AC#5 fail-fast pattern).
"""

from __future__ import annotations

import os
import uuid

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
    assert body == {"issuer": "local", "subject": session.subject}
    client.cookies.clear()
