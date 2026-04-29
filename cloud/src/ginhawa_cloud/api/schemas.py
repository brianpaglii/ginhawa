"""Pydantic v2 request/response schemas.

Each entity has Create, Update, and Read variants. Read schemas use
``ConfigDict(from_attributes=True)`` so they can be constructed from the
SQLAlchemy ORM objects returned by the data-access layer.

Validation responsibilities:
* Date and timestamp inputs are checked against ISO 8601.
* ``Citizen.dob`` must be in the past.
* Enum-like fields use ``Literal`` so the type is preserved in OpenAPI.
* ``Measurement.value`` is range-checked per measurement ``type``.
"""

from datetime import date, datetime
from typing import Generic, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Pagination wrapper used by list endpoints.
# ---------------------------------------------------------------------------
T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


# ---------------------------------------------------------------------------
# Enum aliases — kept in sync with the CHECK constraints in the schema.
# ---------------------------------------------------------------------------
Sex = Literal["M", "F", "O"]
SessionStatus = Literal["in_progress", "completed", "aborted", "error"]
MeasurementPath = Literal["vitals", "anthropometric", "full"]
PrintedStatus = Literal[
    "not_requested",
    "printed_ok",
    "paper_out_pre",
    "paper_out_mid",
    "print_failed",
]
MeasurementType = Literal[
    "systolic_bp",
    "diastolic_bp",
    "spo2",
    "heart_rate",
    "temperature",
    "height",
    "weight",
    "bmi",
]
ActorType = Literal["citizen", "bhw", "system", "admin"]
Role = Literal["bhw", "admin", "data_viewer"]


# ---------------------------------------------------------------------------
# Physiological ranges for measurement values, keyed by measurement type.
# ---------------------------------------------------------------------------
_MEASUREMENT_RANGES: dict[str, tuple[float, float]] = {
    "systolic_bp": (70.0, 250.0),
    "diastolic_bp": (40.0, 150.0),
    "spo2": (70.0, 100.0),
    "heart_rate": (30.0, 220.0),
    "temperature": (30.0, 45.0),
    "height": (80.0, 220.0),
    "weight": (20.0, 250.0),
    "bmi": (10.0, 60.0),
}


def _validate_iso_datetime(v: str) -> str:
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"must be ISO 8601 datetime: {exc}") from exc
    return v


def _validate_iso_date(v: str) -> str:
    try:
        date.fromisoformat(v)
    except ValueError as exc:
        raise ValueError(f"must be ISO 8601 date: {exc}") from exc
    return v


# ---------------------------------------------------------------------------
# Citizen
# ---------------------------------------------------------------------------
class CitizenBase(BaseModel):
    rfid_uid: str
    full_name: str
    dob: str
    sex: Sex
    barangay: str
    phone: str | None = None
    consent_version: str

    @field_validator("dob")
    @classmethod
    def _check_dob(cls, v: str) -> str:
        v = _validate_iso_date(v)
        if date.fromisoformat(v) >= date.today():
            raise ValueError("dob must be in the past")
        return v


class CitizenCreate(CitizenBase):
    registered_by: str | None = None


class CitizenUpdate(BaseModel):
    # extra="forbid" rejects any field not listed below with HTTP 422.
    # Fields like id, rfid_uid, consent_version, consent_given_at, and
    # registered_at are immutable through this endpoint by design.
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    barangay: str | None = None
    phone: str | None = None
    is_active: int | None = None


class CitizenRead(CitizenBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    consent_given_at: str
    registered_at: str
    registered_by: str | None
    is_active: int
    synced: int
    updated_at: str


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class SessionCreate(BaseModel):
    citizen_id: str
    device_id: str
    measurement_path: MeasurementPath | None = None


class SessionUpdate(BaseModel):
    """Mutable subset of a session.

    ``measurement_path`` is intentionally omitted: the citizen's selection
    at the menu screen is fixed once the session is in progress. Other
    immutable fields (``id``, ``citizen_id``, ``device_id``, ``started_at``)
    are absent here by construction; ``extra="forbid"`` makes any attempt
    to PATCH them produce HTTP 422 instead of being silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    ended_at: str | None = None
    status: SessionStatus | None = None
    error_reason: str | None = None
    printed_status: PrintedStatus | None = None

    @field_validator("ended_at")
    @classmethod
    def _check_ended_at(cls, v: str | None) -> str | None:
        return _validate_iso_datetime(v) if v is not None else v


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    citizen_id: str
    device_id: str
    started_at: str
    ended_at: str | None
    status: SessionStatus
    error_reason: str | None
    measurement_path: MeasurementPath | None
    printed_status: PrintedStatus
    synced: int


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
class MeasurementCreate(BaseModel):
    session_id: str
    type: MeasurementType
    value: float
    unit: str
    source_device: str
    validation_notes: str | None = None
    raw_json: str | None = None

    @model_validator(mode="after")
    def _check_value_range(self) -> Self:
        lo, hi = _MEASUREMENT_RANGES[self.type]
        if not (lo <= self.value <= hi):
            raise ValueError(
                f"{self.type} value {self.value} outside physiological range "
                f"[{lo}, {hi}]"
            )
        return self


class MeasurementUpdate(BaseModel):
    # extra="forbid" rejects any field not listed below with HTTP 422.
    # Measurement core data (id, session_id, type, value, unit,
    # source_device, measured_at) is immutable by design — corrections
    # go through PATCH /{id}/invalidate, not through this schema.
    #
    # Note: this schema currently has no consuming endpoint. The
    # extra="forbid" is defensive: when a future route wires
    # MeasurementUpdate in, the contract is already locked.
    model_config = ConfigDict(extra="forbid")

    is_valid: int | None = None
    validation_notes: str | None = None


class MeasurementInvalidate(BaseModel):
    """Body for ``PATCH /measurements/{id}/invalidate``."""

    reason: str = Field(min_length=1)


class MeasurementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    type: MeasurementType
    value: float
    unit: str
    source_device: str
    measured_at: str
    is_valid: int
    validation_notes: str | None
    raw_json: str | None
    synced: int


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------
# AuditLog is append-only by database trigger (see migration:
# audit_log_no_update / audit_log_no_delete). No Update schema is exposed.
class AuditLogCreate(BaseModel):
    actor_type: ActorType
    actor_id: str | None = None
    action: str
    object_type: str | None = None
    object_id: str | None = None
    ip_address: str | None = None
    details: str | None = None


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: str
    actor_type: ActorType
    actor_id: str | None
    action: str
    object_type: str | None
    object_id: str | None
    ip_address: str | None
    details: str | None
    synced: int


# ---------------------------------------------------------------------------
# DeviceConfig
# ---------------------------------------------------------------------------
class DeviceConfigCreate(BaseModel):
    key: str
    value: str


class DeviceConfigUpdate(BaseModel):
    value: str


class DeviceConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    updated_at: str


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
# SECURITY: ``password_hash`` is intentionally absent from every schema in
# this section. Clients submit ``password`` (plaintext) which the route
# handler hashes with argon2id before persisting; the hash NEVER leaves the
# database layer. UserRead also omits the field — credentials must not
# appear in any API response.
class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    role: Role
    assigned_barangay: str | None = None


class UserUpdate(BaseModel):
    # extra="forbid" rejects any field not listed below with HTTP 422.
    # username is immutable through this endpoint (a username change
    # would invalidate audit-log actor_id resolution); password_hash
    # is server-managed (clients send `password` plaintext, which is
    # then hashed); id / created_at / last_login_at are server-managed.
    model_config = ConfigDict(extra="forbid")

    password: str | None = None
    full_name: str | None = None
    role: Role | None = None
    assigned_barangay: str | None = None
    is_active: int | None = None


class UserRead(BaseModel):
    """User read schema — password material is intentionally omitted."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    full_name: str
    role: Role
    assigned_barangay: str | None
    is_active: int
    created_at: str
    last_login_at: str | None
