"""Story 1.5c: object-level authorization matrix (AF-9/NFR-7/AD-3) proven
against harness fixture objects across R-A, R-B, R-X, an expired session,
and a tampered identifier, for every planned descendant route/command class
(query/mutate/print HTTP classes plus the non-HTTP background-command
class). Smallest set that fails if the AD-3 creator-ownership pattern
breaks — not a full test suite.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("requires a live PostgreSQL DATABASE_URL", allow_module_level=True)

from fastapi.testclient import TestClient

from src.adapters import local_identity
from src.adapters.authorization import authorize_owned_row
from src.adapters.models import Session as SessionRecord
from tests.fixtures.authorization_matrix_app import app as harness_app
from tests.fixtures.authorization_matrix_schema import (
    authorization_matrix_fixture_object as fixture_table,
)

client = TestClient(harness_app)

# The three HTTP descendant route/command classes from Task 2 (AR-37/AR-38/AR-41).
_ROUTE_CLASSES = [
    ("query", lambda oid, cookies: client.get(f"/harness/objects/{oid}", cookies=cookies)),
    (
        "command",
        lambda oid, cookies: client.post(f"/harness/objects/{oid}/command", cookies=cookies),
    ),
    ("print", lambda oid, cookies: client.get(f"/harness/objects/{oid}/print", cookies=cookies)),
]


def _email() -> str:
    return f"user-{uuid.uuid4()}@example.com"


def _admitted_identity_and_token(db_session):
    email = _email()
    identity = local_identity.register(db_session, email, "a-fixture-password")
    local_identity.admit_user(db_session, identity.subject)
    session = local_identity.authenticate(db_session, email, "a-fixture-password")
    return identity, session.token


def _create_fixture_object(db_session, owner_subject: str) -> str:
    object_id = str(uuid.uuid4())
    db_session.execute(fixture_table.insert().values(object_id=object_id, owner_subject=owner_subject))
    db_session.commit()
    return object_id


def _expire(db_session, token: str) -> None:
    record = db_session.get(SessionRecord, token)
    assert record is not None, "expected an existing session row to back-date"
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()


def _background_reauthorize(db_session, token: str | None, object_id: str) -> dict | None:
    """Stands in for a coordinator/worker re-check with no HTTP request at all:
    re-derives the identity from its current session state (never a cached
    identity from an earlier HTTP call), re-checks admission independently of
    ownership, then applies the same AD-3 ownership primitive the HTTP route
    classes use. Denies at whichever step fails first."""
    identity = local_identity.validate_session(db_session, token)
    if identity is None:
        return None
    if not local_identity.is_admitted(db_session, identity.subject):
        return None
    return authorize_owned_row(
        db_session,
        identity,
        fixture_table,
        fixture_table.c.object_id,
        fixture_table.c.owner_subject,
        object_id,
    )


def test_owner_succeeds_on_every_route_class(db_session):
    r_a, r_a_token = _admitted_identity_and_token(db_session)
    object_id = _create_fixture_object(db_session, r_a.subject)
    cookies = {"session": r_a_token}

    for name, call in _ROUTE_CLASSES:
        response = call(object_id, cookies)
        assert response.status_code in (200, 202), f"{name} class denied the owner"


def test_cross_owner_and_tampered_identifier_are_denied_and_content_equivalent(db_session):
    r_a, r_a_token = _admitted_identity_and_token(db_session)
    _, r_b_token = _admitted_identity_and_token(db_session)
    object_id = _create_fixture_object(db_session, r_a.subject)
    tampered_object_id = str(uuid.uuid4())  # never inserted -> the "missing" case

    for name, call in _ROUTE_CLASSES:
        cross_owner = call(object_id, {"session": r_b_token})
        tampered = call(tampered_object_id, {"session": r_a_token})

        assert cross_owner.status_code == 404, f"{name} class did not deny cross-owner access"
        assert tampered.status_code == 404, f"{name} class did not deny a tampered identifier"
        # AC#2: no existence leakage -- a real object owned by someone else and a
        # nonexistent object must be indistinguishable to the caller, in both
        # body and headers (content-type/content-length), not status code alone.
        assert cross_owner.status_code == tampered.status_code
        assert cross_owner.json() == tampered.json()
        assert cross_owner.headers["content-type"] == tampered.headers["content-type"]
        assert cross_owner.headers.get("content-length") == tampered.headers.get("content-length")


def test_unadmitted_and_expired_and_tampered_session_are_denied(db_session):
    r_a, _ = _admitted_identity_and_token(db_session)
    object_id = _create_fixture_object(db_session, r_a.subject)

    # R-X: registered, never admitted (Story 1.2's admission layer).
    r_x_email = _email()
    local_identity.register(db_session, r_x_email, "a-fixture-password")
    r_x_session = local_identity.authenticate(db_session, r_x_email, "a-fixture-password")

    # An otherwise-admitted identity whose session has since expired.
    _, expired_token = _admitted_identity_and_token(db_session)
    _expire(db_session, expired_token)

    for name, call in _ROUTE_CLASSES:
        r_x_response = call(object_id, {"session": r_x_session.token})
        expired_response = call(object_id, {"session": expired_token})
        tampered_cookie_response = call(object_id, {"session": "tampered-or-unknown-token"})

        assert r_x_response.status_code == 403, f"{name} class admitted R-X"
        assert expired_response.status_code == 401, f"{name} class admitted an expired session"
        assert tampered_cookie_response.status_code == 401, (
            f"{name} class admitted a tampered session cookie"
        )


def test_background_command_reevaluates_authorization_independently(db_session):
    """AC#1/AC#3: the background/coordinator-style class (no HTTP session, no
    cookie) exercised against the full five-identity matrix, not just a
    subset -- it must re-derive current session state and admission
    independently, rather than trust a cached identity from an earlier HTTP
    request, before applying the same AD-3 ownership primitive the HTTP
    route classes use."""
    r_a, r_a_token = _admitted_identity_and_token(db_session)
    object_id = _create_fixture_object(db_session, r_a.subject)

    # R-A, still valid: succeeds.
    assert _background_reauthorize(db_session, r_a_token, object_id) is not None

    # R-B: admitted, valid session, simply not the owner.
    _, r_b_token = _admitted_identity_and_token(db_session)
    assert _background_reauthorize(db_session, r_b_token, object_id) is None

    # R-X: registered but never admitted -- even given a session token and even
    # if nominally the object's owner, the independent admission recheck denies.
    r_x_email = _email()
    r_x = local_identity.register(db_session, r_x_email, "a-fixture-password")
    r_x_session = local_identity.authenticate(db_session, r_x_email, "a-fixture-password")
    r_x_owned_object_id = _create_fixture_object(db_session, r_x.subject)
    assert _background_reauthorize(db_session, r_x_session.token, r_x_owned_object_id) is None

    # A tampered/nonexistent object identifier, with an otherwise-valid R-A session.
    tampered_object_id = str(uuid.uuid4())
    assert _background_reauthorize(db_session, r_a_token, tampered_object_id) is None

    # An expired session: re-derivation itself fails closed before ownership
    # or admission is even checked.
    _, expired_token = _admitted_identity_and_token(db_session)
    _expire(db_session, expired_token)
    assert _background_reauthorize(db_session, expired_token, object_id) is None

    # A revoked session: same result.
    local_identity.revoke_session(db_session, r_a_token)
    assert _background_reauthorize(db_session, r_a_token, object_id) is None
