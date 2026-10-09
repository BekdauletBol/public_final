"""ORM entities (Section K). Every table exists from the first migration."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from publicsafe.db.base import Base, JSONVariant, UTCDateTime, UUIDStr, new_id, utcnow

CAMERA_STATUSES = ("paired", "online", "offline", "paused", "error", "revoked")
PAIRING_STATUSES = ("pending", "redeemed", "locked", "cancelled")
EVENT_TYPES = ("zone_entered", "zone_cleared", "occupancy_changed")
SECURITY_KINDS = (
    "login_failed",
    "login_ok",
    "redeem_failed",
    "redeem_ok",
    "pin_locked",
    "token_revoked",
    "ws_auth_failed",
    "ws_superseded",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class MonitoringSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_archived_at", "archived_at"),)

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    location_note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    archived_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)

    cameras: Mapped[list[Camera]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in_list(CAMERA_STATUSES)})", name="status"),
        Index("ix_cameras_session_id", "session_id"),
        Index("ix_cameras_status", "status"),
    )

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="paired")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    last_frame_w: Mapped[int | None] = mapped_column(Integer)
    last_frame_h: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)

    session: Mapped[MonitoringSession] = relationship(back_populates="cameras")
    zones: Mapped[list[Zone]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", passive_deletes=True
    )
    credentials: Mapped[list[DeviceCredential]] = relationship(
        back_populates="camera",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="DeviceCredential.camera_id",
    )


class DeviceCredential(Base):
    __tablename__ = "device_credentials"
    __table_args__ = (
        Index("ix_device_credentials_token_hash", "token_hash"),
        Index("ix_device_credentials_camera_id_revoked_at", "camera_id", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    camera_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    created_ip: Mapped[str | None] = mapped_column(String(45))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    revoked_reason: Mapped[str | None] = mapped_column(String(120))

    camera: Mapped[Camera] = relationship(back_populates="credentials", foreign_keys=[camera_id])


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in_list(PAIRING_STATUSES)})", name="status"),
        Index("ix_pairing_codes_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    pin_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    salt: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    redeemed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    redeemed_ip: Mapped[str | None] = mapped_column(String(45))
    redeemed_camera_id: Mapped[str | None] = mapped_column(
        UUIDStr, ForeignKey("cameras.id", ondelete="SET NULL")
    )
    replace_camera_id: Mapped[str | None] = mapped_column(
        UUIDStr, ForeignKey("cameras.id", ondelete="SET NULL")
    )
    camera_name_hint: Mapped[str | None] = mapped_column(String(80))


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint("enter_frames BETWEEN 1 AND 60", name="enter_frames"),
        CheckConstraint("exit_frames BETWEEN 1 AND 60", name="exit_frames"),
        Index("ix_zones_camera_id_deleted_at", "camera_id", "deleted_at"),
    )

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    camera_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    points: Mapped[list[list[float]]] = mapped_column(JSONVariant, nullable=False)
    frame_w: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_h: Mapped[int] = mapped_column(Integer, nullable=False)
    active_rule: Mapped[str] = mapped_column(String(24), nullable=False)
    enter_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    exit_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    camera: Mapped[Camera] = relationship(back_populates="zones")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(f"type IN ({_in_list(EVENT_TYPES)})", name="type"),
        Index("ix_events_camera_id_t_server", "camera_id", "t_server"),
        Index("ix_events_session_id_t_server", "session_id", "t_server"),
        Index("ix_events_type", "type"),
    )

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    camera_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    zone_id: Mapped[str | None] = mapped_column(
        UUIDStr, ForeignKey("zones.id", ondelete="SET NULL")
    )
    zone_name: Mapped[str | None] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    t_server: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    frame_seq: Mapped[int | None] = mapped_column(BigInteger)
    count_before: Mapped[int | None] = mapped_column(Integer)
    count_after: Mapped[int | None] = mapped_column(Integer)
    active_rule: Mapped[str | None] = mapped_column(String(24))
    rule_counts: Mapped[dict[str, int] | None] = mapped_column(JSONVariant)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (Index("ix_metric_samples_camera_id_t", "camera_id", "t"),)

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    camera_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False
    )
    t: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    window_s: Mapped[float | None] = mapped_column(Float)
    fps_in: Mapped[float | None] = mapped_column(Float)
    fps_processed: Mapped[float | None] = mapped_column(Float)
    dropped_replaced: Mapped[int | None] = mapped_column(Integer)
    dropped_ratelimit: Mapped[int | None] = mapped_column(Integer)
    decode_errors: Mapped[int | None] = mapped_column(Integer)
    out_of_order: Mapped[int | None] = mapped_column(Integer)
    total_p50: Mapped[float | None] = mapped_column(Float)
    total_p95: Mapped[float | None] = mapped_column(Float)
    total_p99: Mapped[float | None] = mapped_column(Float)
    infer_p50: Mapped[float | None] = mapped_column(Float)
    infer_p95: Mapped[float | None] = mapped_column(Float)
    queue_wait_p50: Mapped[float | None] = mapped_column(Float)
    rtt_ms: Mapped[float | None] = mapped_column(Float)
    clock_offset_ms: Mapped[float | None] = mapped_column(Float)
    e2e_estimate_p50: Mapped[float | None] = mapped_column(Float)
    server_cpu: Mapped[float | None] = mapped_column(Float)
    server_mem_mb: Mapped[float | None] = mapped_column(Float)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (Index("ix_admin_sessions_expires_at", "expires_at"),)

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(45))


class SecurityLog(Base):
    __tablename__ = "security_log"
    __table_args__ = (
        Index("ix_security_log_t", "t"),
        Index("ix_security_log_kind_t", "kind", "t"),
    )

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_id)
    t: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    camera_id: Mapped[str | None] = mapped_column(UUIDStr)
    session_id: Mapped[str | None] = mapped_column(UUIDStr)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
