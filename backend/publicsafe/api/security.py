"""``GET /api/security/log`` (F-SEC-3)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from publicsafe.api.deps import get_db, require_admin
from publicsafe.api.errors import ApiError
from publicsafe.api.schemas import SecurityLogOut
from publicsafe.db.models import SecurityLog

router = APIRouter(prefix="/api/security", tags=["security"], dependencies=[Depends(require_admin)])


@router.get("/log", response_model=list[SecurityLogOut])
def security_log(
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = Query(None, description="ISO timestamp of the last row seen"),
    db: Session = Depends(get_db),
) -> list[SecurityLogOut]:
    """Newest first; pass the last row's ``t`` as ``cursor`` for the next page."""
    stmt = select(SecurityLog).order_by(SecurityLog.t.desc(), SecurityLog.id.desc()).limit(limit)
    if cursor:
        try:
            before = dt.datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        except ValueError:
            raise ApiError(400, "validation_error", "cursor must be an ISO timestamp") from None
        stmt = stmt.where(SecurityLog.t < before)
    return [SecurityLogOut.model_validate(row) for row in db.scalars(stmt)]
