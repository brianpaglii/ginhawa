"""Wire-format schemas for the kiosk → cloud sync API.

These are kiosk-side mirrors of the cloud's
``ginhawa_cloud.api.schemas.{CitizenSync,SessionSync,MeasurementSync,
BatchSyncResponse}``. We deliberately duplicate them rather than
import across the kiosk/cloud package boundary — the two trees ship
independently, and one taking a transitive dep on the other would
couple their release cadences.

Both sides must round-trip identically. If the cloud adds a field,
the kiosk schema MUST grow it too (otherwise ``extra='forbid'`` on
the cloud will reject every kiosk upload). Treat any cloud-side
sync-schema change as requiring a matching kiosk PR.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


_Sex = Literal["M", "F", "O"]
_SessionStatus = Literal["in_progress", "completed", "aborted", "error"]
_MeasurementPath = Literal["vitals", "anthropometric", "full"]
_PrintedStatus = Literal[
    "not_requested",
    "printed_ok",
    "paper_out_pre",
    "paper_out_mid",
    "print_failed",
]
_MeasurementType = Literal[
    "systolic_bp",
    "diastolic_bp",
    "spo2",
    "heart_rate",
    "temperature",
    "height",
    "weight",
    "bmi",
]
SyncStatus = Literal[
    "created",
    "updated",
    "conflict_stale",
    "conflict_constraint",
    "rejected",
]


class CitizenSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    rfid_uid: str
    full_name: str
    dob: str
    sex: _Sex
    barangay: str
    phone: str | None = None
    consent_version: str
    consent_given_at: str
    registered_at: str
    registered_by: str | None = None
    is_active: int
    updated_at: str


class SessionSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    citizen_id: str
    device_id: str
    started_at: str
    ended_at: str | None = None
    status: _SessionStatus
    error_reason: str | None = None
    measurement_path: _MeasurementPath | None = None
    printed_status: _PrintedStatus
    synced: int
    updated_at: str


class MeasurementSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    type: _MeasurementType
    value: float
    unit: str
    source_device: str
    measured_at: str
    is_valid: int = 1
    validation_notes: str | None = None
    raw_json: str | None = None
    synced: int
    updated_at: str


class BatchSyncRecordResult(BaseModel):
    id: str
    status: SyncStatus
    error: str | None = None


class BatchSyncResponse(BaseModel):
    results: list[BatchSyncRecordResult]
