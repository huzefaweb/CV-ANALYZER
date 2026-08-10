"""Auth0IdentityAdapter (AD-21): deferred until AUTH0_* env vars are configured.

Mirrors local_identity's function names/arity (IDENTITY_PORT_FUNCTIONS in
src/domain/identity.py) so switching the active adapter later needs no
downstream rework. Not wired to a live Auth0 tenant in this story — AC#7
explicitly defers that preflight rather than failing the story over
unconfigured credentials. Every function raises unconditionally: nothing
calls this module while AUTH0_* env vars are absent (api.py only mounts
identity_router — the local adapter's routes — in that state).
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from ..domain.identity import Identity


class Auth0NotConfigured(Exception):
    """Raised if any Auth0 adapter function is called before AC#7's deferred preflight runs."""


def register(db: OrmSession, email: str, password: str) -> Identity:
    raise Auth0NotConfigured("Auth0 adapter preflight is deferred (AC#7) until AUTH0_* env vars are configured.")


def authenticate(db: OrmSession, email: str, password: str) -> Identity:
    raise Auth0NotConfigured("Auth0 adapter preflight is deferred (AC#7) until AUTH0_* env vars are configured.")


def validate_session(db: OrmSession, token: str | None) -> Identity | None:
    raise Auth0NotConfigured("Auth0 adapter preflight is deferred (AC#7) until AUTH0_* env vars are configured.")


def revoke_session(db: OrmSession, token: str | None) -> None:
    raise Auth0NotConfigured("Auth0 adapter preflight is deferred (AC#7) until AUTH0_* env vars are configured.")
