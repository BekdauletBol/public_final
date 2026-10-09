"""TEST_MODE-only endpoints (Section K). Mounted only when ``TEST_MODE=1``."""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from publicsafe.api.deps import get_db, require_admin
from publicsafe.api.ratelimit import client_ip
from publicsafe.api.schemas import CameraSettings, TestCameraCreate, TestCameraOut
from publicsafe.db.models import Camera, DeviceCredential
from publicsafe.db.repo import get_session_or_404

router = APIRouter(prefix="/api/test", tags=["test"], dependencies=[Depends(require_admin)])


@router.post("/cameras", response_model=TestCameraOut, status_code=status.HTTP_201_CREATED)
def create_test_camera(
    body: TestCameraCreate, request: Request, db: Session = Depends(get_db)
) -> TestCameraOut:
    """Create a paired camera with a device token, skipping the PIN flow."""
    session = get_session_or_404(db, body.session_id)
    camera = Camera(
        session_id=session.id,
        name=body.name,
        device_label=body.device_label,
        status="paired",
        settings=CameraSettings().model_dump(),
    )
    db.add(camera)
    db.flush()
    token = secrets.token_urlsafe(32)
    db.add(
        DeviceCredential(
            camera_id=camera.id,
            token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            created_ip=client_ip(request),
        )
    )
    db.flush()
    return TestCameraOut(camera_id=camera.id, session_id=session.id, device_token=token)
