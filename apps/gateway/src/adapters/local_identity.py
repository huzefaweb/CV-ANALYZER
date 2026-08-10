"""LocalCredentialIdentityAdapter (AD-21): V1-default email/password adapter.

Registration and authentication run directly against the application's user
table. Passwords are stored only as argon2 hashes — never plaintext, never
reversibly encrypted, never logged. Sessions are opaque server-side records
(no JWT) so FastAPI validates them the same fail-closed way it would Auth0
JWT claims, but by DB lookup instead of signature check.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..domain.identity import LOCAL_ISSUER, Identity
from .models import Session as SessionRecord
from .models import User

SESSION_TTL = timedelta(hours=24)

_hasher = PasswordHasher()


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    """Neutral failure: covers unknown email and wrong password alike."""


@dataclass(frozen=True)
class LocalSession:
    token: str
    identity: Identity


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def register(db: OrmSession, email: str, password: str) -> Identity:
    email = _normalize_email(email)
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise EmailAlreadyRegistered
    user = User(
        email=email,
        password_hash=_hasher.hash(password),
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Race: a concurrent registration for the same email committed first.
        db.rollback()
        raise EmailAlreadyRegistered from exc
    return Identity(issuer=LOCAL_ISSUER, subject=user.id)


def authenticate(db: OrmSession, email: str, password: str) -> LocalSession:
    email = _normalize_email(email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        raise InvalidCredentials
    try:
        _hasher.verify(user.password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
        # argon2-cffi's own documented exception set for verify(): wrong
        # password, verification failure, and a corrupted/foreign hash
        # (InvalidHashError — notably a ValueError subclass, not Argon2Error)
        # all fail closed as the same neutral InvalidCredentials.
        raise InvalidCredentials from exc
    return _issue_session(db, user.id)


def _issue_session(db: OrmSession, user_id: str) -> LocalSession:
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    db.add(
        SessionRecord(
            token=token,
            user_id=user_id,
            issued_at=now,
            expires_at=now + SESSION_TTL,
        )
    )
    db.commit()
    return LocalSession(token=token, identity=Identity(issuer=LOCAL_ISSUER, subject=user_id))


def validate_session(db: OrmSession, token: str | None) -> Identity | None:
    """Fail closed: missing, malformed, expired, revoked, or unknown tokens all return None."""
    if not token:
        return None
    record = db.get(SessionRecord, token)
    if record is None:
        return None
    if record.revoked_at is not None:
        return None
    if record.expires_at <= datetime.now(timezone.utc):
        return None
    return Identity(issuer=LOCAL_ISSUER, subject=record.user_id)


def revoke_session(db: OrmSession, token: str | None) -> None:
    """Idempotent: revoking a missing/already-revoked token is a no-op, not an error."""
    if not token:
        return
    record = db.get(SessionRecord, token)
    if record is None or record.revoked_at is not None:
        return
    record.revoked_at = datetime.now(timezone.utc)
    db.commit()


def is_admitted(db: OrmSession, subject: str) -> bool:
    user = db.get(User, subject)
    return user is not None and user.admitted_at is not None


def admit_user(db: OrmSession, subject: str) -> None:
    """Demo Operator admission action (fixture/manual — no public admin route in V1)."""
    user = db.get(User, subject)
    if user is not None and user.admitted_at is None:
        user.admitted_at = datetime.now(timezone.utc)
        db.commit()
