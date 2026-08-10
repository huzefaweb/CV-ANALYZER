"""FastAPI identity router and fail-closed session verifier (AR-5/AR-6/AR-7).

Only the local adapter's routes are mounted while AUTH0_* env vars are
absent (AD-21) — the inactive adapter is never reachable. All failure
responses are neutral: no admission/account/credential detail leaks through
status code or body regardless of which fail-closed condition triggered.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session as OrmSession

from ..domain.identity import Identity
from . import local_identity
from .db import get_db

router = APIRouter(prefix="/identity", tags=["identity"])

SESSION_COOKIE = "session"

# Bounds match users.email VARCHAR(320) (migration) and a conventional
# minimum password floor — enforced here so a violation is a clean 422,
# never a DB error or an unbounded-cost argon2 hash of an empty string.
_EMAIL_MAX_LENGTH = 320
_PASSWORD_MIN_LENGTH = 8
_PASSWORD_MAX_LENGTH = 256

_NEUTRAL_AUTH_FAILURE = HTTPException(status_code=401, detail="Authentication failed")
_NEUTRAL_ADMISSION_FAILURE = HTTPException(status_code=403, detail="Access not available")


class RegisterRequest(BaseModel):
    email: EmailStr = Field(max_length=_EMAIL_MAX_LENGTH)
    password: str = Field(min_length=_PASSWORD_MIN_LENGTH, max_length=_PASSWORD_MAX_LENGTH)


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=_EMAIL_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=_PASSWORD_MAX_LENGTH)


@router.post("/register", status_code=201)
def register(body: RegisterRequest, db: OrmSession = Depends(get_db)) -> dict[str, str]:
    try:
        local_identity.register(db, body.email, body.password)
    except local_identity.EmailAlreadyRegistered:
        # Neutral: does not confirm/deny which email is already registered.
        raise HTTPException(status_code=409, detail="Registration could not be completed") from None
    return {"status": "registered"}


@router.post("/login")
def login(body: LoginRequest, response: Response, db: OrmSession = Depends(get_db)) -> dict[str, str]:
    try:
        session = local_identity.authenticate(db, body.email, body.password)
    except local_identity.InvalidCredentials:
        raise _NEUTRAL_AUTH_FAILURE from None
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(local_identity.SESSION_TTL.total_seconds()),
    )
    return {"status": "authenticated"}


@router.post("/logout")
def logout(
    response: Response,
    db: OrmSession = Depends(get_db),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, str]:
    local_identity.revoke_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "logged_out"}


def require_session(
    db: OrmSession = Depends(get_db),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Identity:
    identity = local_identity.validate_session(db, session_token)
    if identity is None:
        raise _NEUTRAL_AUTH_FAILURE
    return identity


def require_admitted_identity(
    identity: Identity = Depends(require_session),
    db: OrmSession = Depends(get_db),
) -> Identity:
    if not local_identity.is_admitted(db, identity.subject):
        raise _NEUTRAL_ADMISSION_FAILURE
    return identity


@router.get("/session")
def whoami(identity: Identity = Depends(require_admitted_identity)) -> dict[str, str]:
    return {"issuer": identity.issuer, "subject": identity.subject}
