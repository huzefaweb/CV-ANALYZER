"""AC#1, #2, #4, #5: local adapter round trip, admission fixtures, fail-closed
session validation, and idempotent logout — smallest set that fails if any
of Story 1.2's invariants break.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.adapters import local_identity
from src.adapters.models import Session as SessionRecord
from src.domain.identity import LOCAL_ISSUER


def _email() -> str:
    return f"user-{uuid.uuid4()}@example.com"


def test_register_then_authenticate_round_trip(db_session):
    email = _email()
    registered = local_identity.register(db_session, email, "correct horse battery staple")
    assert registered.issuer == LOCAL_ISSUER
    session = local_identity.authenticate(db_session, email, "correct horse battery staple")
    assert session.identity == registered
    assert local_identity.validate_session(db_session, session.token) == registered


def test_register_rejects_duplicate_email(db_session):
    email = _email()
    local_identity.register(db_session, email, "password one two three")
    try:
        local_identity.register(db_session, email, "a different password")
        assert False, "expected EmailAlreadyRegistered"
    except local_identity.EmailAlreadyRegistered:
        pass


def test_authenticate_rejects_wrong_password_and_unknown_email(db_session):
    email = _email()
    local_identity.register(db_session, email, "the-real-password")
    for attempt_email, attempt_password in (
        (email, "the-wrong-password"),
        (_email(), "irrelevant password"),
    ):
        try:
            local_identity.authenticate(db_session, attempt_email, attempt_password)
            assert False, "expected InvalidCredentials"
        except local_identity.InvalidCredentials:
            pass


def test_password_never_stored_in_plaintext(db_session):
    email = _email()
    password = "super-secret-value"
    local_identity.register(db_session, email, password)

    from sqlalchemy import select
    from src.adapters.models import User

    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    assert password not in user.password_hash
    assert user.password_hash != password


def test_fail_closed_session_validation(db_session):
    email = _email()
    local_identity.register(db_session, email, "a-fixture-password")
    session = local_identity.authenticate(db_session, email, "a-fixture-password")

    # missing
    assert local_identity.validate_session(db_session, None) is None
    # malformed / tampered / unknown
    assert local_identity.validate_session(db_session, "not-a-real-token") is None

    # expired
    record = db_session.get(SessionRecord, session.token)
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert local_identity.validate_session(db_session, session.token) is None

    # revoked
    fresh = local_identity.authenticate(db_session, email, "a-fixture-password")
    local_identity.revoke_session(db_session, fresh.token)
    assert local_identity.validate_session(db_session, fresh.token) is None


def test_logout_is_idempotent(db_session):
    email = _email()
    local_identity.register(db_session, email, "a-fixture-password")
    session = local_identity.authenticate(db_session, email, "a-fixture-password")

    local_identity.revoke_session(db_session, session.token)
    local_identity.revoke_session(db_session, session.token)  # second call: no-op, no error
    local_identity.revoke_session(db_session, None)  # missing token: no-op, no error
    assert local_identity.validate_session(db_session, session.token) is None


def test_authenticate_with_corrupted_hash_is_neutral(db_session):
    """A malformed/foreign password_hash must fail closed as InvalidCredentials,
    not leak a raw argon2 exception (e.g. InvalidHashError) as a 500."""
    from sqlalchemy import select

    from src.adapters.models import User

    email = _email()
    local_identity.register(db_session, email, "a-fixture-password")
    user = db_session.execute(select(User).where(User.email == email)).scalar_one()
    user.password_hash = "not-a-real-argon2-hash"
    db_session.commit()

    try:
        local_identity.authenticate(db_session, email, "a-fixture-password")
        assert False, "expected InvalidCredentials"
    except local_identity.InvalidCredentials:
        pass


def test_repeated_login_is_safe_and_does_not_corrupt_state(db_session):
    """AC#5 'duplicate login attempts ... do not duplicate state' means
    retry-safety, not a single-session-per-user cap (which would break
    legitimate multi-device login) — each attempt issues its own valid
    session, and neither corrupts or invalidates the other.
    """
    email = _email()
    local_identity.register(db_session, email, "a-fixture-password")

    first = local_identity.authenticate(db_session, email, "a-fixture-password")
    second = local_identity.authenticate(db_session, email, "a-fixture-password")

    assert first.token != second.token
    assert local_identity.validate_session(db_session, first.token) == first.identity
    assert local_identity.validate_session(db_session, second.token) == second.identity

    local_identity.revoke_session(db_session, first.token)
    assert local_identity.validate_session(db_session, first.token) is None
    assert local_identity.validate_session(db_session, second.token) == second.identity


def test_normalization_and_email_case_insensitivity(db_session):
    local_identity.register(db_session, "  Mixed.Case@Example.com  ", "a-fixture-password")
    try:
        local_identity.register(db_session, "mixed.case@example.com", "a-different-password")
        assert False, "expected EmailAlreadyRegistered for a case-insensitive duplicate"
    except local_identity.EmailAlreadyRegistered:
        pass

    session = local_identity.authenticate(db_session, "MIXED.CASE@EXAMPLE.COM", "a-fixture-password")
    assert session.identity.issuer == LOCAL_ISSUER


def test_admission_fixtures_r_a_r_b_r_x(db_session):
    """AF-1 identities provisioned through the local adapter (AC#2)."""
    r_a = local_identity.register(db_session, _email(), "fixture-password-a")
    r_b = local_identity.register(db_session, _email(), "fixture-password-b")
    r_x = local_identity.register(db_session, _email(), "fixture-password-x")

    local_identity.admit_user(db_session, r_a.subject)
    local_identity.admit_user(db_session, r_b.subject)

    assert local_identity.is_admitted(db_session, r_a.subject) is True
    assert local_identity.is_admitted(db_session, r_b.subject) is True
    assert local_identity.is_admitted(db_session, r_x.subject) is False

    # immutable local subject IDs identify each fixture, not email
    assert r_a.subject != r_b.subject != r_x.subject
