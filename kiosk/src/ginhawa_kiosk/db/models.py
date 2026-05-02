"""Kiosk-side SQLAlchemy models.

Mirrors the kiosk subset of ``/schema.sql``:

* ``citizens``      — the local registry, populated by RFID enrolment.
* ``sessions``      — one row per kiosk visit.
* ``measurements``  — one row per captured vital sign.
* ``audit_log``     — append-only local audit trail (mirrors the cloud's
  enforcement; the kiosk's own `record_audit` is the only writer).
* ``device_config`` — per-kiosk key-value settings (kiosk_id, deployment
  barangay, calibration timestamps, consent_version).

CLOUD-ONLY tables are intentionally absent here:
* ``users``              — BHW portal accounts; no concept on the kiosk.
* ``device_credentials`` — the cloud knows about kiosks; the kiosk does
                           not maintain a registry of itself.

Type substitutions vs ``/schema.sql``: TEXT → String, REAL → Float,
INTEGER → Integer. SQLite under SQLCipher honours these natively.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SEX_VALUES = ("M", "F", "O")
_SESSION_STATUS_VALUES = ("in_progress", "completed", "aborted", "error")
_MEASUREMENT_PATH_VALUES = ("vitals", "anthropometric", "full")
_PRINTED_STATUS_VALUES = (
    "not_requested",
    "printed_ok",
    "paper_out_pre",
    "paper_out_mid",
    "print_failed",
)
_MEASUREMENT_TYPE_VALUES = (
    "systolic_bp",
    "diastolic_bp",
    "spo2",
    "heart_rate",
    "temperature",
    "height",
    "weight",
    "bmi",
)
# Kiosk omits 'admin' (BHW portal only) but keeps 'kiosk' for self-service
# attribution and 'bhw' for the future kiosk-side BHW UI.
_ACTOR_TYPE_VALUES = ("citizen", "bhw", "system", "kiosk")


def _in_constraint(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({rendered})"


class Citizen(Base):
    __tablename__ = "citizens"
    __table_args__ = (
        CheckConstraint(_in_constraint("sex", _SEX_VALUES), name="ck_citizens_sex"),
        CheckConstraint("is_active IN (0, 1)", name="ck_citizens_is_active"),
        CheckConstraint("synced IN (0, 1)", name="ck_citizens_synced"),
        Index("idx_citizens_rfid", "rfid_uid", unique=True),
        Index("idx_citizens_barangay", "barangay"),
        Index("idx_citizens_active_sync", "is_active", "synced"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    rfid_uid: Mapped[str] = mapped_column()
    full_name: Mapped[str] = mapped_column()
    dob: Mapped[str] = mapped_column()
    sex: Mapped[str] = mapped_column()
    barangay: Mapped[str] = mapped_column()
    phone: Mapped[str | None] = mapped_column()
    consent_version: Mapped[str] = mapped_column()
    consent_given_at: Mapped[str] = mapped_column(default=_utc_now_iso)
    registered_at: Mapped[str] = mapped_column(default=_utc_now_iso)
    registered_by: Mapped[str | None] = mapped_column()
    is_active: Mapped[int] = mapped_column(default=1)
    synced: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[str] = mapped_column(default=_utc_now_iso)

    sessions: Mapped[list["Session"]] = relationship(back_populates="citizen")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            _in_constraint("status", _SESSION_STATUS_VALUES),
            name="ck_sessions_status",
        ),
        CheckConstraint(
            _in_constraint("measurement_path", _MEASUREMENT_PATH_VALUES),
            name="ck_sessions_measurement_path",
        ),
        CheckConstraint(
            _in_constraint("printed_status", _PRINTED_STATUS_VALUES),
            name="ck_sessions_printed_status",
        ),
        CheckConstraint("synced IN (0, 1)", name="ck_sessions_synced"),
        Index("idx_sessions_citizen", "citizen_id", "started_at"),
        Index("idx_sessions_sync", "synced", "ended_at"),
        Index("idx_sessions_status", "status"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    citizen_id: Mapped[str] = mapped_column(
        ForeignKey("citizens.id", ondelete="RESTRICT")
    )
    device_id: Mapped[str] = mapped_column()
    started_at: Mapped[str] = mapped_column(default=_utc_now_iso)
    ended_at: Mapped[str | None] = mapped_column()
    status: Mapped[str] = mapped_column(default="in_progress")
    error_reason: Mapped[str | None] = mapped_column()
    measurement_path: Mapped[str | None] = mapped_column()
    printed_status: Mapped[str] = mapped_column(default="not_requested")
    synced: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[str] = mapped_column(default=_utc_now_iso)

    citizen: Mapped["Citizen"] = relationship(back_populates="sessions")
    measurements: Mapped[list["Measurement"]] = relationship(
        back_populates="session", passive_deletes=True
    )


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        CheckConstraint(
            _in_constraint("type", _MEASUREMENT_TYPE_VALUES),
            name="ck_measurements_type",
        ),
        CheckConstraint("is_valid IN (0, 1)", name="ck_measurements_is_valid"),
        CheckConstraint("synced IN (0, 1)", name="ck_measurements_synced"),
        Index("idx_meas_session", "session_id"),
        Index("idx_meas_type_time", "type", "measured_at"),
        Index("idx_meas_sync", "synced"),
        Index("idx_meas_valid", "is_valid"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column()
    value: Mapped[float] = mapped_column()
    unit: Mapped[str] = mapped_column()
    source_device: Mapped[str] = mapped_column()
    measured_at: Mapped[str] = mapped_column(default=_utc_now_iso)
    is_valid: Mapped[int] = mapped_column(default=1)
    validation_notes: Mapped[str | None] = mapped_column()
    raw_json: Mapped[str | None] = mapped_column()
    synced: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[str] = mapped_column(default=_utc_now_iso)

    session: Mapped["Session"] = relationship(back_populates="measurements")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            _in_constraint("actor_type", _ACTOR_TYPE_VALUES),
            name="ck_audit_log_actor_type",
        ),
        CheckConstraint("synced IN (0, 1)", name="ck_audit_log_synced"),
        Index("idx_audit_time", "timestamp"),
        Index("idx_audit_actor", "actor_type", "actor_id"),
        Index("idx_audit_object", "object_type", "object_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(default=_utc_now_iso)
    actor_type: Mapped[str] = mapped_column()
    actor_id: Mapped[str | None] = mapped_column()
    action: Mapped[str] = mapped_column()
    object_type: Mapped[str | None] = mapped_column()
    object_id: Mapped[str | None] = mapped_column()
    ip_address: Mapped[str | None] = mapped_column()
    details: Mapped[str | None] = mapped_column()
    synced: Mapped[int] = mapped_column(default=0)


class DeviceConfig(Base):
    __tablename__ = "device_config"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column()
    updated_at: Mapped[str] = mapped_column(default=_utc_now_iso)
