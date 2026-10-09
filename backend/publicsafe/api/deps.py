"""FastAPI dependencies: database session, admin authentication and CSRF check."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from publicsafe.api.errors import ApiError
from publicsafe.db.base import session_scope
from publicsafe.db.models import AdminSession

#: Custom header required on every state-changing admin request (Section D).
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "publicsafe"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_db(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.sessionmaker) as db:
        yield db


@dataclass(frozen=True)
class AdminContext:
    username: str
    token: str
    session: AdminSession


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminContext:
    """Admin cookie session plus the CSRF header on non-GET requests (F-SEC-1)."""
    from publicsafe.api.auth import COOKIE_NAME  # local import avoids a cycle

    token = request.cookies.get(COOKIE_NAME, "")
    auth = request.app.state.admin_auth
    row = auth.resolve(db, token) if token else None
    if row is None:
        raise ApiError(401, "unauthenticated", "Admin login required")
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "csrf_failed", f"Missing header X-Requested-With: {CSRF_VALUE}")
    return AdminContext(username=auth.username, token=token, session=row)
