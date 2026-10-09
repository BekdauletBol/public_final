"""The single error shape used by every HTTP endpoint (Section F)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

#: Default ``error.code`` per HTTP status when a handler gives none.
STATUS_CODES: dict[int, str] = {
    400: "validation_error",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    423: "locked",
    429: "rate_limited",
    500: "internal_error",
}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


class ApiError(HTTPException):
    """An HTTP error with an explicit machine-readable ``code``."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.message = message
        self.details = details or {}
