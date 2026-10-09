"""Initial schema: every table from spec Section K (reviewed by hand, M1).

Revision ID: 0001
Revises:
Create Date: 2026-10-09 21:52:05.061301+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "admin_sessions",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_admin_sessions")),
    )
    with op.batch_alter_table("admin_sessions", schema=None) as batch_op:
        batch_op.create_index("ix_admin_sessions_expires_at", ["expires_at"], unique=False)

    op.create_table(
        "security_log",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column("t", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column(
            "camera_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "details", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_log")),
    )
    with op.batch_alter_table("security_log", schema=None) as batch_op:
        batch_op.create_index("ix_security_log_kind_t", ["kind", "t"], unique=False)
        batch_op.create_index("ix_security_log_t", ["t"], unique=False)

    op.create_table(
        "sessions",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("location_note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index("ix_sessions_archived_at", ["archived_at"], unique=False)

    op.create_table(
        "cameras",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("device_label", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "settings", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_frame_w", sa.Integer(), nullable=True),
        sa.Column("last_frame_h", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('paired', 'online', 'offline', 'paused', 'error', 'revoked')",
            name=op.f("ck_cameras_status"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_cameras_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cameras")),
    )
    with op.batch_alter_table("cameras", schema=None) as batch_op:
        batch_op.create_index("ix_cameras_session_id", ["session_id"], unique=False)
        batch_op.create_index("ix_cameras_status", ["status"], unique=False)

    op.create_table(
        "device_credentials",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "camera_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_ip", sa.String(length=45), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_device_credentials_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_credentials")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_device_credentials_token_hash")),
    )
    with op.batch_alter_table("device_credentials", schema=None) as batch_op:
        batch_op.create_index(
            "ix_device_credentials_camera_id_revoked_at", ["camera_id", "revoked_at"], unique=False
        )
        batch_op.create_index("ix_device_credentials_token_hash", ["token_hash"], unique=False)

    op.create_table(
        "metric_samples",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "camera_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column("t", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_s", sa.Float(), nullable=True),
        sa.Column("fps_in", sa.Float(), nullable=True),
        sa.Column("fps_processed", sa.Float(), nullable=True),
        sa.Column("dropped_replaced", sa.Integer(), nullable=True),
        sa.Column("dropped_ratelimit", sa.Integer(), nullable=True),
        sa.Column("decode_errors", sa.Integer(), nullable=True),
        sa.Column("out_of_order", sa.Integer(), nullable=True),
        sa.Column("total_p50", sa.Float(), nullable=True),
        sa.Column("total_p95", sa.Float(), nullable=True),
        sa.Column("total_p99", sa.Float(), nullable=True),
        sa.Column("infer_p50", sa.Float(), nullable=True),
        sa.Column("infer_p95", sa.Float(), nullable=True),
        sa.Column("queue_wait_p50", sa.Float(), nullable=True),
        sa.Column("rtt_ms", sa.Float(), nullable=True),
        sa.Column("clock_offset_ms", sa.Float(), nullable=True),
        sa.Column("e2e_estimate_p50", sa.Float(), nullable=True),
        sa.Column("server_cpu", sa.Float(), nullable=True),
        sa.Column("server_mem_mb", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_metric_samples_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_samples")),
    )
    with op.batch_alter_table("metric_samples", schema=None) as batch_op:
        batch_op.create_index("ix_metric_samples_camera_id_t", ["camera_id", "t"], unique=False)

    op.create_table(
        "pairing_codes",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column("pin_hash", sa.String(length=64), nullable=False),
        sa.Column("salt", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_ip", sa.String(length=45), nullable=True),
        sa.Column(
            "redeemed_camera_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "replace_camera_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=True,
        ),
        sa.Column("camera_name_hint", sa.String(length=80), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'redeemed', 'locked', 'cancelled')",
            name=op.f("ck_pairing_codes_status"),
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_camera_id"],
            ["cameras.id"],
            name=op.f("fk_pairing_codes_redeemed_camera_id_cameras"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["replace_camera_id"],
            ["cameras.id"],
            name=op.f("fk_pairing_codes_replace_camera_id_cameras"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_pairing_codes_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pairing_codes")),
    )
    with op.batch_alter_table("pairing_codes", schema=None) as batch_op:
        batch_op.create_index(
            "ix_pairing_codes_status_expires_at", ["status", "expires_at"], unique=False
        )

    op.create_table(
        "zones",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "camera_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column(
            "points", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("frame_w", sa.Integer(), nullable=False),
        sa.Column("frame_h", sa.Integer(), nullable=False),
        sa.Column("active_rule", sa.String(length=24), nullable=False),
        sa.Column("enter_frames", sa.Integer(), nullable=False),
        sa.Column("exit_frames", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("enter_frames BETWEEN 1 AND 60", name=op.f("ck_zones_enter_frames")),
        sa.CheckConstraint("exit_frames BETWEEN 1 AND 60", name=op.f("ck_zones_exit_frames")),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_zones_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zones")),
    )
    with op.batch_alter_table("zones", schema=None) as batch_op:
        batch_op.create_index(
            "ix_zones_camera_id_deleted_at", ["camera_id", "deleted_at"], unique=False
        )

    op.create_table(
        "events",
        sa.Column(
            "id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "camera_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "zone_id",
            sa.String(length=36).with_variant(sa.UUID(as_uuid=False), "postgresql"),
            nullable=True,
        ),
        sa.Column("zone_name", sa.String(length=80), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("t_server", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frame_seq", sa.BigInteger(), nullable=True),
        sa.Column("count_before", sa.Integer(), nullable=True),
        sa.Column("count_after", sa.Integer(), nullable=True),
        sa.Column("active_rule", sa.String(length=24), nullable=True),
        sa.Column(
            "rule_counts", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('zone_entered', 'zone_cleared', 'occupancy_changed')",
            name=op.f("ck_events_type"),
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_events_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_events_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zones.id"], name=op.f("fk_events_zone_id_zones"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_events_camera_id_t_server", ["camera_id", "t_server"], unique=False
        )
        batch_op.create_index(
            "ix_events_session_id_t_server", ["session_id", "t_server"], unique=False
        )
        batch_op.create_index("ix_events_type", ["type"], unique=False)


def downgrade() -> None:

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_type")
        batch_op.drop_index("ix_events_session_id_t_server")
        batch_op.drop_index("ix_events_camera_id_t_server")

    op.drop_table("events")
    with op.batch_alter_table("zones", schema=None) as batch_op:
        batch_op.drop_index("ix_zones_camera_id_deleted_at")

    op.drop_table("zones")
    with op.batch_alter_table("pairing_codes", schema=None) as batch_op:
        batch_op.drop_index("ix_pairing_codes_status_expires_at")

    op.drop_table("pairing_codes")
    with op.batch_alter_table("metric_samples", schema=None) as batch_op:
        batch_op.drop_index("ix_metric_samples_camera_id_t")

    op.drop_table("metric_samples")
    with op.batch_alter_table("device_credentials", schema=None) as batch_op:
        batch_op.drop_index("ix_device_credentials_token_hash")
        batch_op.drop_index("ix_device_credentials_camera_id_revoked_at")

    op.drop_table("device_credentials")
    with op.batch_alter_table("cameras", schema=None) as batch_op:
        batch_op.drop_index("ix_cameras_status")
        batch_op.drop_index("ix_cameras_session_id")

    op.drop_table("cameras")
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_sessions_archived_at")

    op.drop_table("sessions")
    with op.batch_alter_table("security_log", schema=None) as batch_op:
        batch_op.drop_index("ix_security_log_t")
        batch_op.drop_index("ix_security_log_kind_t")

    op.drop_table("security_log")
    with op.batch_alter_table("admin_sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_admin_sessions_expires_at")

    op.drop_table("admin_sessions")
