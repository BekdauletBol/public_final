"""PIN pairing (Section G, F.3): mint, list, cancel, redeem, precheck."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from publicsafe import __version__
from publicsafe.api.deps import get_db, require_admin
from publicsafe.api.errors import ApiError
from publicsafe.api.ratelimit import client_ip, enforce
from publicsafe.api.schemas import (
    CameraSettings,
    PairingCodeCreate,
    PairingCodeMinted,
    PairingCodeOut,
    PrecheckOut,
    RedeemIn,
    RedeemOut,
)
from publicsafe.config import Settings
from publicsafe.core.timing import now_ms
from publicsafe.db.base import utcnow
from publicsafe.db.models import Camera, DeviceCredential, MonitoringSession, PairingCode
from publicsafe.db.repo import (
    get_camera_or_404,
    get_session_or_404,
    log_security,
    revoke_credentials,
)
from publicsafe.pairing import (
    PinFormatError,
    format_pin,
    generate_pin,
    hash_device_token,
    hash_pin,
    new_device_token,
    new_salt,
    normalise_pin,
    pin_matches,
)

log = logging.getLogger("publicsafe.pairing")

admin_router = APIRouter(prefix="/api", tags=["pairing"], dependencies=[Depends(require_admin)])
public_router = APIRouter(prefix="/api/pair", tags=["pairing"])


def base_url(request: Request, settings: Settings) -> str:
    """Origin for pairing URLs: PUBLIC_URL or the request's own host (works behind Vite)."""
    if settings.public_url:
        return settings.public_url.rstrip("/")
    host = request.headers.get("host") or request.url.netloc
    return f"{request.url.scheme}://{host}"


def ws_url(origin: str) -> str:
    if origin.startswith("https://"):
        return "wss://" + origin[len("https://") :] + "/ws/camera"
    if origin.startswith("http://"):
        return "ws://" + origin[len("http://") :] + "/ws/camera"
    return origin + "/ws/camera"


def effective_status(code: PairingCode, now: dt.datetime) -> str:
    if code.status == "pending" and code.expires_at <= now:
        return "expired"
    return code.status


def _code_out(code: PairingCode, now: dt.datetime) -> PairingCodeOut:
    return PairingCodeOut(
        code_id=code.id,
        status=effective_status(code, now),
        created_at=code.created_at,
        expires_at=code.expires_at,
        attempts=code.attempts,
        max_attempts=code.max_attempts,
        redeemed_camera_id=code.redeemed_camera_id,
        replace_camera_id=code.replace_camera_id,
        camera_name_hint=code.camera_name_hint,
    )


def _pending_codes(db: Session, now: dt.datetime) -> list[PairingCode]:
    return list(
        db.scalars(
            select(PairingCode).where(PairingCode.status == "pending", PairingCode.expires_at > now)
        )
    )


# --- admin -----------------------------------------------------------------


@admin_router.post(
    "/sessions/{session_id}/pairing-codes",
    response_model=PairingCodeMinted,
    status_code=status.HTTP_201_CREATED,
)
def mint_code(
    session_id: str, body: PairingCodeCreate, request: Request, db: Session = Depends(get_db)
) -> PairingCodeMinted:
    settings: Settings = request.app.state.settings
    session = get_session_or_404(db, session_id)
    if session.archived_at is not None:
        raise ApiError(409, "session_archived", "Unarchive the session before pairing phones")
    if body.replace_camera_id is not None:
        camera = get_camera_or_404(db, body.replace_camera_id)
        if camera.session_id != session.id:
            raise ApiError(
                400,
                "validation_error",
                "replace_camera_id must belong to this session",
                {"fields": {"replace_camera_id": "wrong session"}},
            )

    now = utcnow()
    pending = _pending_codes(db, now)
    for _ in range(100):
        pin = generate_pin(settings.pin_alphabet)
        if not any(pin_matches(c.salt, pin, c.pin_hash) for c in pending):
            break
    else:  # pragma: no cover - 20^6 space; practically unreachable
        raise ApiError(500, "internal_error", "Could not allocate a unique PIN")

    salt = new_salt()
    code = PairingCode(
        session_id=session.id,
        pin_hash=hash_pin(salt, pin),
        salt=salt,
        status="pending",
        attempts=0,
        max_attempts=settings.pin_max_attempts,
        expires_at=now + dt.timedelta(seconds=settings.pin_ttl_seconds),
        replace_camera_id=body.replace_camera_id,
        camera_name_hint=body.camera_name_hint,
    )
    db.add(code)
    db.flush()
    return PairingCodeMinted(
        code_id=code.id,
        pin=format_pin(pin),
        expires_at=code.expires_at,
        pair_url=f"{base_url(request, settings)}/pair?c={code.id}",
        ttl_seconds=settings.pin_ttl_seconds,
    )


@admin_router.get("/sessions/{session_id}/pairing-codes", response_model=list[PairingCodeOut])
def list_codes(session_id: str, db: Session = Depends(get_db)) -> list[PairingCodeOut]:
    get_session_or_404(db, session_id)
    now = utcnow()
    rows = db.scalars(
        select(PairingCode)
        .where(PairingCode.session_id == session_id)
        .order_by(PairingCode.created_at.desc())
    )
    return [_code_out(c, now) for c in rows]


@admin_router.delete("/pairing-codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_code(code_id: str, db: Session = Depends(get_db)) -> Response:
    code = db.get(PairingCode, code_id)
    if code is None:
        raise ApiError(404, "code_not_found", "Pairing code not found")
    if code.status == "pending":
        code.status = "cancelled"
        db.flush()
    elif code.status != "cancelled":
        raise ApiError(409, "code_not_pending", f"Code is already {code.status}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- public ----------------------------------------------------------------


@public_router.get("/precheck", response_model=PrecheckOut)
def precheck() -> PrecheckOut:
    return PrecheckOut(server_time_ms=now_ms(), version=__version__)


def _fail(
    db: Session,
    ip: str,
    http_status: int,
    code: str,
    message: str,
    *,
    reason: str,
    code_id: str | None = None,
    session_id: str | None = None,
) -> ApiError:
    """Log the failure (never the PIN), commit the log, and return the error to raise."""
    details: dict[str, object] = {"reason": reason, "code_known": code_id is not None}
    log_security(db, "redeem_failed", ip, session_id=session_id, details=details)
    db.commit()
    return ApiError(http_status, code, message)


@public_router.post("/redeem", response_model=RedeemOut)
def redeem(body: RedeemIn, request: Request, db: Session = Depends(get_db)) -> RedeemOut:
    settings: Settings = request.app.state.settings
    enforce(request.app.state.limiters["redeem"], request)
    ip = client_ip(request)

    try:
        pin = normalise_pin(body.pin, settings.pin_alphabet)
    except PinFormatError:
        # No database access and no attempt counted (G.2 step 2).
        raise ApiError(404, "pin_invalid", "Invalid or unknown PIN") from None

    now = utcnow()
    candidates: list[PairingCode]
    if body.code_id is not None:
        code = db.get(PairingCode, body.code_id)
        if code is None:
            raise _fail(db, ip, 404, "pin_invalid", "Invalid or unknown PIN", reason="unknown_code")
        candidates = [code]
    else:
        candidates = _pending_codes(db, now)

    match = next((c for c in candidates if pin_matches(c.salt, pin, c.pin_hash)), None)
    if match is None:
        if body.code_id is not None:
            code = candidates[0]
            code.attempts += 1
            if code.attempts >= code.max_attempts:
                if code.status == "pending":
                    code.status = "locked"
                    log_security(
                        db,
                        "pin_locked",
                        ip,
                        session_id=code.session_id,
                        details={"attempts": code.attempts},
                    )
                raise _fail(
                    db,
                    ip,
                    423,
                    "pin_locked",
                    "Too many wrong attempts; ask the admin for a new code",
                    reason="wrong_pin",
                    code_id=code.id,
                    session_id=code.session_id,
                )
            raise _fail(
                db,
                ip,
                404,
                "pin_invalid",
                "Invalid or unknown PIN",
                reason="wrong_pin",
                code_id=code.id,
                session_id=code.session_id,
            )
        raise _fail(db, ip, 404, "pin_invalid", "Invalid or unknown PIN", reason="wrong_pin")

    # Atomic single-use claim (G.2 step 5).
    claimed = db.execute(
        update(PairingCode)
        .where(
            PairingCode.id == match.id,
            PairingCode.status == "pending",
            PairingCode.expires_at > now,
        )
        .values(status="redeemed", redeemed_at=now, redeemed_ip=ip)
    ).rowcount
    if claimed != 1:
        db.rollback()  # re-read in a fresh transaction to see the winner's commit
        current = db.get(PairingCode, match.id)
        state = effective_status(current, now) if current else "cancelled"
        mapping = {
            "redeemed": (409, "pin_already_redeemed", "This code was already used"),
            "locked": (423, "pin_locked", "Too many wrong attempts; ask the admin for a new code"),
            "cancelled": (404, "pin_invalid", "Invalid or unknown PIN"),
            "expired": (410, "pin_expired", "This code has expired; ask the admin for a new one"),
        }
        http_status, code_name, message = mapping.get(state, mapping["cancelled"])
        raise _fail(
            db,
            ip,
            http_status,
            code_name,
            message,
            reason=state,
            code_id=match.id,
            session_id=match.session_id,
        )
    db.refresh(match)

    session = db.get(MonitoringSession, match.session_id)
    if session is None or session.archived_at is not None:
        match.status = "cancelled"
        raise _fail(
            db,
            ip,
            410,
            "session_gone",
            "This monitoring session is no longer accepting phones",
            reason="session_gone",
            code_id=match.id,
            session_id=match.session_id,
        )

    replaced_camera_id: str | None = None
    camera = db.get(Camera, match.replace_camera_id) if match.replace_camera_id else None
    if camera is not None:
        revoke_credentials(db, camera.id, "replaced", now)
        camera.status = "paired"
        camera.revoked_at = None
        if body.device_label:
            camera.device_label = body.device_label
        replaced_camera_id = camera.id
    else:
        count = db.scalar(select(func.count(Camera.id)).where(Camera.session_id == session.id)) or 0
        camera = Camera(
            session_id=session.id,
            name=match.camera_name_hint or body.device_label or f"Camera {count + 1}",
            device_label=body.device_label,
            status="paired",
            settings=CameraSettings().model_dump(),
        )
        db.add(camera)
        db.flush()

    token = new_device_token()
    db.add(
        DeviceCredential(camera_id=camera.id, token_hash=hash_device_token(token), created_ip=ip)
    )
    match.redeemed_camera_id = camera.id
    log_security(
        db,
        "redeem_ok",
        ip,
        camera_id=camera.id,
        session_id=session.id,
        details={"remember": body.remember, "replaced": replaced_camera_id is not None},
    )
    db.flush()
    db.commit()  # make the credential visible before the phone connects

    if replaced_camera_id is not None:
        closer = getattr(request.app.state, "close_camera_sockets", None)
        if closer is not None:
            closer([replaced_camera_id], 4003, "replaced")

    origin = base_url(request, settings)
    return RedeemOut(
        camera_id=camera.id,
        camera_name=camera.name,
        session_name=session.name,
        device_token=token,
        ws_url=ws_url(origin),
        settings=CameraSettings.model_validate(camera.settings),
    )
