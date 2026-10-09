"""Admin authentication: argon2id password check, server-side cookie sessions (F-ADM-1/2)."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from publicsafe.api.deps import AdminContext, get_db, require_admin
from publicsafe.api.errors import ApiError
from publicsafe.api.ratelimit import client_ip, enforce
from publicsafe.api.schemas import LoginIn, MeOut
from publicsafe.db.models import AdminSession
from publicsafe.db.repo import log_security

COOKIE_NAME = "ps_admin"
#: How often an active session's idle expiry is pushed forward (avoids a write per request).
TOUCH_INTERVAL = dt.timedelta(seconds=60)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AdminAuth:
    """Password verification with constant-time behaviour for unknown usernames.

    An unknown username is verified against a dummy hash with the same argon2
    parameters as the real one, so the response time does not reveal whether
    the username exists.
    """

    def __init__(self, username: str, password_hash: str, idle: dt.timedelta) -> None:
        self.username = username
        self.password_hash = password_hash
        self.idle = idle
        self._hasher = PasswordHasher()
        params = extract_parameters(password_hash)  # raises InvalidHashError
        self._dummy_hash = PasswordHasher.from_parameters(params).hash(secrets.token_urlsafe(16))

    def verify(self, username: str, password: str) -> bool:
        known = hmac.compare_digest(username.encode(), self.username.encode())
        target = self.password_hash if known else self._dummy_hash
        try:
            ok = self._hasher.verify(target, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        return ok and known

    def create_session(self, db: Session, ip: str | None) -> tuple[str, dt.datetime]:
        token = secrets.token_urlsafe(32)  # 32 random bytes
        now = dt.datetime.now(dt.UTC)
        expires = now + self.idle
        db.add(
            AdminSession(
                id_hash=hash_token(token),
                created_at=now,
                expires_at=expires,
                last_seen_at=now,
                ip=ip,
            )
        )
        db.flush()
        return token, expires

    def resolve(self, db: Session, token: str) -> AdminSession | None:
        """Return the live session for ``token`` (sliding idle expiry) or None."""
        if not token or len(token) > 128:
            return None
        row = db.get(AdminSession, hash_token(token))
        if row is None:
            return None
        now = dt.datetime.now(dt.UTC)
        if row.expires_at <= now:
            db.delete(row)
            db.flush()
            return None
        if now - row.last_seen_at >= TOUCH_INTERVAL:
            row.last_seen_at = now
            row.expires_at = now + self.idle
            db.flush()
        return row

    def destroy(self, db: Session, token: str) -> None:
        db.execute(delete(AdminSession).where(AdminSession.id_hash == hash_token(token)))

    @staticmethod
    def destroy_all(db: Session) -> int:
        return db.execute(delete(AdminSession)).rowcount

    @staticmethod
    def purge_expired(db: Session) -> int:
        now = dt.datetime.now(dt.UTC)
        return db.execute(delete(AdminSession).where(AdminSession.expires_at <= now)).rowcount


@dataclass(frozen=True)
class CookiePolicy:
    name: str = COOKIE_NAME
    secure: bool = True
    samesite: str = "strict"
    path: str = "/"


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE_NAME, "", max_age=0, path="/", secure=True, httponly=True, samesite="strict"
    )


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> Response:
    enforce(request.app.state.limiters["login"], request)
    auth: AdminAuth = request.app.state.admin_auth
    ip = client_ip(request)
    if not auth.verify(body.username, body.password):
        log_security(db, "login_failed", ip, details={"username_length": len(body.username)})
        db.commit()  # the 401 below rolls the request session back; keep the log row
        raise ApiError(401, "invalid_credentials", "Invalid credentials")
    token, _expires = auth.create_session(db, ip)
    log_security(db, "login_ok", ip)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_session_cookie(response, token, int(auth.idle.total_seconds()))
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, admin: AdminContext = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    request.app.state.admin_auth.destroy(db, admin.token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/me", response_model=MeOut)
def me(admin: AdminContext = Depends(require_admin)) -> MeOut:
    return MeOut(username=admin.username, session_expires_at=admin.session.expires_at)


__all__ = ["COOKIE_NAME", "AdminAuth", "router", "select"]
