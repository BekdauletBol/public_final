"""Pydantic request/response models for the HTTP API (Section F)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from publicsafe.core.rules import DEFAULT_RULE, RuleId


def iso_ms(value: dt.datetime) -> str:
    """ISO-8601 UTC with milliseconds, e.g. ``2026-10-10T12:00:00.123Z``."""
    value = value.astimezone(dt.UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


UtcMs = Annotated[dt.datetime, PlainSerializer(iso_ms, return_type=str)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


# --- auth ------------------------------------------------------------------


class LoginIn(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class MeOut(BaseModel):
    username: str
    session_expires_at: UtcMs


# --- cameras ---------------------------------------------------------------


class CameraSettings(StrictModel):
    target_fps: int = Field(10, ge=1, le=30)
    jpeg_quality: int = Field(65, ge=30, le=95)
    max_long_edge: int = Field(1280, ge=640, le=1920)
    confidence: float = Field(0.30, ge=0.05, le=0.95)
    privacy_mode: bool = False


class CameraSettingsPatch(StrictModel):
    target_fps: int | None = Field(None, ge=1, le=30)
    jpeg_quality: int | None = Field(None, ge=30, le=95)
    max_long_edge: int | None = Field(None, ge=640, le=1920)
    confidence: float | None = Field(None, ge=0.05, le=0.95)
    privacy_mode: bool | None = None


class CameraPatch(StrictModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    settings: CameraSettingsPatch | None = None

    _strip_name = field_validator("name", mode="before")(_strip)


class RevokeIn(StrictModel):
    reason: str | None = Field(None, max_length=120)


class CameraSummary(ApiModel):
    id: str
    session_id: str
    name: str
    status: str
    device_label: str | None
    created_at: UtcMs
    last_seen_at: UtcMs | None
    fps_in: float | None = None
    fps_processed: float | None = None
    drop_ratio: float | None = None
    rtt_ms: float | None = None
    clock_offset_ms: float | None = None
    occupancy: dict[str, int] = Field(default_factory=dict)
    zone_count: int = 0
    needs_review: bool = False


class CameraDetail(CameraSummary):
    settings: CameraSettings
    zones: list[ZoneOut]
    health: dict[str, Any]


# --- sessions --------------------------------------------------------------


class SessionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    location_note: str | None = Field(None, max_length=200)

    _strip = field_validator("name", "location_note", mode="before")(_strip)


class SessionPatch(StrictModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    location_note: str | None = Field(None, max_length=200)
    archived: bool | None = None

    _strip = field_validator("name", "location_note", mode="before")(_strip)


class SessionOut(ApiModel):
    id: str
    name: str
    location_note: str | None
    created_at: UtcMs
    archived_at: UtcMs | None
    camera_count: int = 0
    online_count: int = 0


class SessionDetail(SessionOut):
    cameras: list[CameraSummary]


# --- zones -----------------------------------------------------------------


class ZoneCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    points: list[list[float]] = Field(min_length=1, max_length=64)
    frame_w: int = Field(ge=1, le=10000)
    frame_h: int = Field(ge=1, le=10000)
    active_rule: RuleId = DEFAULT_RULE
    enter_frames: int = Field(3, ge=1, le=60)
    exit_frames: int = Field(5, ge=1, le=60)
    enabled: bool = True

    _strip_name = field_validator("name", mode="before")(_strip)


class ZonePatch(StrictModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    points: list[list[float]] | None = Field(None, min_length=1, max_length=64)
    frame_w: int | None = Field(None, ge=1, le=10000)
    frame_h: int | None = Field(None, ge=1, le=10000)
    active_rule: RuleId | None = None
    enter_frames: int | None = Field(None, ge=1, le=60)
    exit_frames: int | None = Field(None, ge=1, le=60)
    enabled: bool | None = None

    _strip_name = field_validator("name", mode="before")(_strip)


class ZoneOut(ApiModel):
    id: str
    camera_id: str
    name: str
    points: list[list[float]]
    frame_w: int
    frame_h: int
    active_rule: str
    enter_frames: int
    exit_frames: int
    enabled: bool
    needs_review: bool
    created_at: UtcMs
    updated_at: UtcMs


# --- pairing (Section G) ---------------------------------------------------


class PairingCodeCreate(StrictModel):
    camera_name_hint: str | None = Field(None, max_length=80)
    replace_camera_id: str | None = Field(None, max_length=36)

    _strip = field_validator("camera_name_hint", mode="before")(_strip)


class PairingCodeMinted(BaseModel):
    code_id: str
    pin: str
    expires_at: UtcMs
    pair_url: str
    pair_url_warning: str | None = None
    ttl_seconds: int


class PairingCodeOut(BaseModel):
    code_id: str
    status: str
    created_at: UtcMs
    expires_at: UtcMs
    attempts: int
    max_attempts: int
    redeemed_camera_id: str | None
    replace_camera_id: str | None
    camera_name_hint: str | None


class RedeemIn(StrictModel):
    pin: str = Field(min_length=1, max_length=32)
    code_id: str | None = Field(None, max_length=36)
    device_label: str | None = Field(None, max_length=60)
    remember: bool = False

    _strip = field_validator("device_label", mode="before")(_strip)


class RedeemOut(BaseModel):
    camera_id: str
    camera_name: str
    session_name: str
    device_token: str
    ws_url: str
    settings: CameraSettings


class PrecheckOut(BaseModel):
    server_time_ms: float
    version: str


# --- security log ----------------------------------------------------------


class SecurityLogOut(ApiModel):
    id: str
    t: UtcMs
    kind: str
    ip: str | None
    camera_id: str | None
    session_id: str | None
    details: dict[str, Any] | None


# --- TEST_MODE -------------------------------------------------------------


class TestCameraCreate(StrictModel):
    session_id: str
    name: str = Field("test-camera", min_length=1, max_length=80)
    device_label: str | None = Field(None, max_length=60)


class TestCameraOut(BaseModel):
    camera_id: str
    session_id: str
    device_token: str


class TestDetectorScript(StrictModel):
    script: list[list[dict[str, Any]]] = Field(max_length=10_000)


CameraDetail.model_rebuild()
