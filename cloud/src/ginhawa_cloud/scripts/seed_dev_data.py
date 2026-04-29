"""GINHAWA development-data seeder.

Idempotent script that populates the cloud database with a small,
realistic dataset for local development and smoke testing. Re-running
this script is safe: every seeded entity has a hardcoded UUID and the
script skips creation when a row with that UUID already exists.

Run via::

    uv run python -m ginhawa_cloud.scripts.seed_dev_data

Requires ``DATABASE_URL`` and ``JWT_SECRET`` to be set (e.g. via
``cloud/.env``).

What gets seeded
----------------
* 1 admin user (username: ``admin``)
* 3 BHW users (one per seeded barangay)
* 20 citizens distributed 8/7/5 across Tibagan / Pinaglabanan /
  Corazon de Jesus
* 5 sample sessions across two of the citizens
* 15 sample measurements across those sessions

Every seeded row produces an ``audit_log`` entry attributed to
``actor_type='system'`` / ``actor_id='seed_script'``, so the database
has a realistic audit history after seeding.

Deferred
--------
The original Phase 1 plan also called for seeding a device credential.
That is skipped here because the ``device_credentials`` table does not
exist yet — it will be added with the kiosk-sync feature (originally
Phase 1 Prompt 9). The script's stdout makes the deferral explicit at
end-of-run.

Credentials produced (DEV ONLY — DO NOT USE IN PROD)
----------------------------------------------------
* ``admin`` / ``seed_admin_password_change_me``
* ``bhw_tibagan`` / ``seed_bhw_password``
* ``bhw_pinaglabanan`` / ``seed_bhw_password``
* ``bhw_corazon`` / ``seed_bhw_password``
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.security import hash_password
from ..db.models import AuditLog, Citizen, Measurement, User
from ..db.models import Session as SessionModel
from ..services.audit import record_audit


# ---------------------------------------------------------------------------
# Constants used for audit-row attribution. Every seeded row produces an
# audit_log entry with these values.
# ---------------------------------------------------------------------------
_SEED_ACTOR_TYPE = "system"
_SEED_ACTOR_ID = "seed_script"

_ADMIN_PASSWORD = "seed_admin_password_change_me"  # pragma: allowlist secret
_BHW_PASSWORD = "seed_bhw_password"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Hardcoded UUIDs. Idempotency keys: every seed function checks for the row
# at its UUID before inserting and skips on hit. UUIDs are grouped in
# numerical bands so a glance at the audit_log can identify seeded data.
#   ...0001        — admin
#   ...0010..0012  — three BHW users
#   ...0101..0120  — twenty citizens
#   ...0201..0205  — five sessions
#   ...0301..0315  — fifteen measurements
# ---------------------------------------------------------------------------
_ADMIN_ID = "00000000-0000-0000-0000-000000000001"

_BHW_USERS: list[dict[str, str]] = [
    {
        "id": "00000000-0000-0000-0000-000000000010",
        "username": "bhw_tibagan",
        "full_name": "Anna Reyes",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000011",
        "username": "bhw_pinaglabanan",
        "full_name": "Carlos Mendoza",
        "barangay": "Pinaglabanan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000012",
        "username": "bhw_corazon",
        "full_name": "Maria Tan",
        "barangay": "Corazon de Jesus",
    },
]

# 20 citizens distributed 8 / 7 / 5 across Tibagan / Pinaglabanan / Corazon
# de Jesus. Sex split is 10F / 10M.
_CITIZENS: list[dict[str, str]] = [
    # Tibagan (8)
    {
        "id": "00000000-0000-0000-0000-000000000101",
        "rfid_uid": "SEED_CARD_0001",
        "name": "Maria Dela Cruz",
        "dob": "1955-03-14",
        "sex": "F",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000102",
        "rfid_uid": "SEED_CARD_0002",
        "name": "Juan Dela Cruz",
        "dob": "1960-05-19",
        "sex": "M",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000103",
        "rfid_uid": "SEED_CARD_0003",
        "name": "Ana Santos",
        "dob": "1968-07-22",
        "sex": "F",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000104",
        "rfid_uid": "SEED_CARD_0004",
        "name": "Jose Santos",
        "dob": "1972-09-11",
        "sex": "M",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000105",
        "rfid_uid": "SEED_CARD_0005",
        "name": "Rosa Reyes",
        "dob": "1975-11-08",
        "sex": "F",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000106",
        "rfid_uid": "SEED_CARD_0006",
        "name": "Pedro Reyes",
        "dob": "1985-04-03",
        "sex": "M",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000107",
        "rfid_uid": "SEED_CARD_0007",
        "name": "Carmen Garcia",
        "dob": "1982-01-30",
        "sex": "F",
        "barangay": "Tibagan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000108",
        "rfid_uid": "SEED_CARD_0008",
        "name": "Mario Garcia",
        "dob": "1995-12-25",
        "sex": "M",
        "barangay": "Tibagan",
    },
    # Pinaglabanan (7)
    {
        "id": "00000000-0000-0000-0000-000000000109",
        "rfid_uid": "SEED_CARD_0009",
        "name": "Teresa Mendoza",
        "dob": "1958-08-15",
        "sex": "F",
        "barangay": "Pinaglabanan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000110",
        "rfid_uid": "SEED_CARD_0010",
        "name": "Antonio Mendoza",
        "dob": "1965-10-20",
        "sex": "M",
        "barangay": "Pinaglabanan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000111",
        "rfid_uid": "SEED_CARD_0011",
        "name": "Luz Bautista",
        "dob": "1973-02-28",
        "sex": "F",
        "barangay": "Pinaglabanan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000112",
        "rfid_uid": "SEED_CARD_0012",
        "name": "Roberto Bautista",
        "dob": "1978-03-05",
        "sex": "M",
        "barangay": "Pinaglabanan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000113",
        "rfid_uid": "SEED_CARD_0013",
        "name": "Linda Aquino",
        "dob": "1990-06-17",
        "sex": "F",
        "barangay": "Pinaglabanan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000114",
        "rfid_uid": "SEED_CARD_0014",
        "name": "Eduardo Aquino",
        "dob": "1988-11-12",
        "sex": "M",
        "barangay": "Pinaglabanan",
    },
    {
        "id": "00000000-0000-0000-0000-000000000115",
        "rfid_uid": "SEED_CARD_0015",
        "name": "Carlos Lim",
        "dob": "2000-07-04",
        "sex": "M",
        "barangay": "Pinaglabanan",
    },
    # Corazon de Jesus (5)
    {
        "id": "00000000-0000-0000-0000-000000000116",
        "rfid_uid": "SEED_CARD_0016",
        "name": "Cristina Lim",
        "dob": "1962-04-09",
        "sex": "F",
        "barangay": "Corazon de Jesus",
    },
    {
        "id": "00000000-0000-0000-0000-000000000117",
        "rfid_uid": "SEED_CARD_0017",
        "name": "Manuel Ramos",
        "dob": "1970-06-30",
        "sex": "M",
        "barangay": "Corazon de Jesus",
    },
    {
        "id": "00000000-0000-0000-0000-000000000118",
        "rfid_uid": "SEED_CARD_0018",
        "name": "Patricia Ramos",
        "dob": "1980-09-23",
        "sex": "F",
        "barangay": "Corazon de Jesus",
    },
    {
        "id": "00000000-0000-0000-0000-000000000119",
        "rfid_uid": "SEED_CARD_0019",
        "name": "Ricardo Tan",
        "dob": "1987-08-18",
        "sex": "M",
        "barangay": "Corazon de Jesus",
    },
    {
        "id": "00000000-0000-0000-0000-000000000120",
        "rfid_uid": "SEED_CARD_0020",
        "name": "Gloria Tan",
        "dob": "1992-12-01",
        "sex": "F",
        "barangay": "Corazon de Jesus",
    },
]

# 5 sessions across two of the Tibagan citizens (Maria 0101 and Juan 0102).
# Time offsets are hours-from-now; negative values are in the past. The
# in_progress session is 2 hours ago to today.
_SESSIONS: list[dict[str, Any]] = [
    {
        "id": "00000000-0000-0000-0000-000000000201",
        "citizen_id": "00000000-0000-0000-0000-000000000101",
        "device_id": "seed-kiosk-001",
        "started_offset_hours": -33.0,
        "ended_offset_hours": -32.75,
        "status": "completed",
        "printed_status": "printed_ok",
        "measurement_path": "vitals",
        "error_reason": None,
    },
    {
        "id": "00000000-0000-0000-0000-000000000202",
        "citizen_id": "00000000-0000-0000-0000-000000000101",
        "device_id": "seed-kiosk-001",
        "started_offset_hours": -28.0,
        "ended_offset_hours": -27.67,
        "status": "completed",
        "printed_status": "printed_ok",
        "measurement_path": "vitals",
        "error_reason": None,
    },
    {
        "id": "00000000-0000-0000-0000-000000000203",
        "citizen_id": "00000000-0000-0000-0000-000000000102",
        "device_id": "seed-kiosk-001",
        "started_offset_hours": -25.0,
        "ended_offset_hours": -24.7,
        "status": "completed",
        "printed_status": "printed_ok",
        "measurement_path": "anthropometric",
        "error_reason": None,
    },
    {
        "id": "00000000-0000-0000-0000-000000000204",
        "citizen_id": "00000000-0000-0000-0000-000000000102",
        "device_id": "seed-kiosk-001",
        "started_offset_hours": -2.0,
        "ended_offset_hours": None,
        "status": "in_progress",
        "printed_status": "not_requested",
        "measurement_path": "full",
        "error_reason": None,
    },
    {
        "id": "00000000-0000-0000-0000-000000000205",
        "citizen_id": "00000000-0000-0000-0000-000000000101",
        "device_id": "seed-kiosk-001",
        "started_offset_hours": -22.0,
        "ended_offset_hours": -21.92,
        "status": "aborted",
        "printed_status": "not_requested",
        "measurement_path": "vitals",
        "error_reason": "user_walked_away",
    },
]

# 15 measurements distributed 3 / 4 / 3 / 2 / 3 across the five sessions.
# Values are well within the schema's CHECK ranges; units match the API's
# expected-units table so is_valid stays 1.
_MEASUREMENTS: list[dict[str, Any]] = [
    # Session 0201 (Maria, completed) — 3 vitals
    {
        "id": "00000000-0000-0000-0000-000000000301",
        "session_id": "00000000-0000-0000-0000-000000000201",
        "type": "systolic_bp",
        "value": 122.0,
        "unit": "mmHg",
        "minutes_after_start": 2,
    },
    {
        "id": "00000000-0000-0000-0000-000000000302",
        "session_id": "00000000-0000-0000-0000-000000000201",
        "type": "diastolic_bp",
        "value": 78.0,
        "unit": "mmHg",
        "minutes_after_start": 2,
    },
    {
        "id": "00000000-0000-0000-0000-000000000303",
        "session_id": "00000000-0000-0000-0000-000000000201",
        "type": "spo2",
        "value": 97.0,
        "unit": "%",
        "minutes_after_start": 4,
    },
    # Session 0202 (Maria, completed) — 4 vitals
    {
        "id": "00000000-0000-0000-0000-000000000304",
        "session_id": "00000000-0000-0000-0000-000000000202",
        "type": "systolic_bp",
        "value": 118.0,
        "unit": "mmHg",
        "minutes_after_start": 1,
    },
    {
        "id": "00000000-0000-0000-0000-000000000305",
        "session_id": "00000000-0000-0000-0000-000000000202",
        "type": "diastolic_bp",
        "value": 82.0,
        "unit": "mmHg",
        "minutes_after_start": 1,
    },
    {
        "id": "00000000-0000-0000-0000-000000000306",
        "session_id": "00000000-0000-0000-0000-000000000202",
        "type": "spo2",
        "value": 98.0,
        "unit": "%",
        "minutes_after_start": 3,
    },
    {
        "id": "00000000-0000-0000-0000-000000000307",
        "session_id": "00000000-0000-0000-0000-000000000202",
        "type": "heart_rate",
        "value": 72.0,
        "unit": "bpm",
        "minutes_after_start": 4,
    },
    # Session 0203 (Juan, completed, anthropometric) — 3 anthropometric
    {
        "id": "00000000-0000-0000-0000-000000000308",
        "session_id": "00000000-0000-0000-0000-000000000203",
        "type": "temperature",
        "value": 36.8,
        "unit": "°C",
        "minutes_after_start": 1,
    },
    {
        "id": "00000000-0000-0000-0000-000000000309",
        "session_id": "00000000-0000-0000-0000-000000000203",
        "type": "height",
        "value": 168.0,
        "unit": "cm",
        "minutes_after_start": 5,
    },
    {
        "id": "00000000-0000-0000-0000-000000000310",
        "session_id": "00000000-0000-0000-0000-000000000203",
        "type": "weight",
        "value": 72.0,
        "unit": "kg",
        "minutes_after_start": 8,
    },
    # Session 0204 (Juan, in_progress) — 2 partial measurements
    {
        "id": "00000000-0000-0000-0000-000000000311",
        "session_id": "00000000-0000-0000-0000-000000000204",
        "type": "heart_rate",
        "value": 78.0,
        "unit": "bpm",
        "minutes_after_start": 2,
    },
    {
        "id": "00000000-0000-0000-0000-000000000312",
        "session_id": "00000000-0000-0000-0000-000000000204",
        "type": "temperature",
        "value": 36.5,
        "unit": "°C",
        "minutes_after_start": 5,
    },
    # Session 0205 (Maria, aborted) — 3 vitals captured before abort
    {
        "id": "00000000-0000-0000-0000-000000000313",
        "session_id": "00000000-0000-0000-0000-000000000205",
        "type": "systolic_bp",
        "value": 145.0,
        "unit": "mmHg",
        "minutes_after_start": 1,
    },
    {
        "id": "00000000-0000-0000-0000-000000000314",
        "session_id": "00000000-0000-0000-0000-000000000205",
        "type": "diastolic_bp",
        "value": 92.0,
        "unit": "mmHg",
        "minutes_after_start": 1,
    },
    {
        "id": "00000000-0000-0000-0000-000000000315",
        "session_id": "00000000-0000-0000-0000-000000000205",
        "type": "spo2",
        "value": 95.0,
        "unit": "%",
        "minutes_after_start": 3,
    },
]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@dataclass
class SeedReport:
    users_created: int
    citizens_created: int
    sessions_created: int
    measurements_created: int
    audit_rows_written: int


def seed(db: Session) -> SeedReport:
    """Populate the database. Idempotent.

    The function is safe to call against either an empty database or
    one that already has some seeded rows: each helper checks for the
    hardcoded UUID of its target before inserting.

    Caller controls the transaction. We commit once at the end so the
    whole seed lands atomically; on partial failure the caller should
    roll back and re-run (idempotency keeps re-runs safe).
    """
    initial_audit_count = _count(db, AuditLog)

    users_created = _seed_admin(db) + _seed_bhws(db)
    citizens_created = _seed_citizens(db)
    sessions_created = _seed_sessions(db)
    measurements_created = _seed_measurements(db)

    db.commit()

    audit_rows_written = _count(db, AuditLog) - initial_audit_count
    return SeedReport(
        users_created=users_created,
        citizens_created=citizens_created,
        sessions_created=sessions_created,
        measurements_created=measurements_created,
        audit_rows_written=audit_rows_written,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count(db: Session, model: Any) -> int:
    return db.execute(select(func.count(model.id))).scalar_one()


def _record(
    db: Session,
    *,
    object_type: str,
    object_id: str,
    details: dict[str, Any],
) -> None:
    record_audit(
        db,
        action="create",
        actor_type=_SEED_ACTOR_TYPE,
        actor_id=_SEED_ACTOR_ID,
        object_type=object_type,
        object_id=object_id,
        details=details,
    )


def _seed_admin(db: Session) -> int:
    if db.get(User, _ADMIN_ID) is not None:
        return 0
    user = User(
        id=_ADMIN_ID,
        username="admin",
        password_hash=hash_password(_ADMIN_PASSWORD),
        full_name="Seeded Admin",
        role="admin",
        assigned_barangay=None,
        is_active=1,
        created_at=_utc_now_iso(),
        last_login_at=None,
    )
    db.add(user)
    _record(
        db,
        object_type="user",
        object_id=user.id,
        details={"username": user.username, "role": user.role},
    )
    return 1


def _seed_bhws(db: Session) -> int:
    created = 0
    for bhw in _BHW_USERS:
        if db.get(User, bhw["id"]) is not None:
            continue
        user = User(
            id=bhw["id"],
            username=bhw["username"],
            password_hash=hash_password(_BHW_PASSWORD),
            full_name=bhw["full_name"],
            role="bhw",
            assigned_barangay=bhw["barangay"],
            is_active=1,
            created_at=_utc_now_iso(),
            last_login_at=None,
        )
        db.add(user)
        _record(
            db,
            object_type="user",
            object_id=user.id,
            details={
                "username": user.username,
                "role": user.role,
                "barangay": bhw["barangay"],
            },
        )
        created += 1
    return created


def _seed_citizens(db: Session) -> int:
    created = 0
    now = _utc_now_iso()
    for c in _CITIZENS:
        if db.get(Citizen, c["id"]) is not None:
            continue
        citizen = Citizen(
            id=c["id"],
            rfid_uid=c["rfid_uid"],
            full_name=c["name"],
            dob=c["dob"],
            sex=c["sex"],
            barangay=c["barangay"],
            phone=None,
            consent_version="1.0",
            consent_given_at=now,
            registered_at=now,
            registered_by=_SEED_ACTOR_ID,
            is_active=1,
            synced=0,
            updated_at=now,
        )
        db.add(citizen)
        _record(
            db,
            object_type="citizen",
            object_id=citizen.id,
            details={
                "rfid_uid": citizen.rfid_uid,
                "barangay": citizen.barangay,
            },
        )
        created += 1
    return created


def _seed_sessions(db: Session) -> int:
    created = 0
    now = datetime.now(timezone.utc)
    for s in _SESSIONS:
        if db.get(SessionModel, s["id"]) is not None:
            continue
        started_at = (now + timedelta(hours=s["started_offset_hours"])).isoformat()
        ended_at: str | None = None
        if s["ended_offset_hours"] is not None:
            ended_at = (now + timedelta(hours=s["ended_offset_hours"])).isoformat()
        session = SessionModel(
            id=s["id"],
            citizen_id=s["citizen_id"],
            device_id=s["device_id"],
            started_at=started_at,
            ended_at=ended_at,
            status=s["status"],
            error_reason=s["error_reason"],
            measurement_path=s["measurement_path"],
            printed_status=s["printed_status"],
            synced=0,
        )
        db.add(session)
        _record(
            db,
            object_type="session",
            object_id=session.id,
            details={
                "citizen_id": session.citizen_id,
                "device_id": session.device_id,
                "status": session.status,
            },
        )
        created += 1
    return created


def _seed_measurements(db: Session) -> int:
    created = 0
    now = datetime.now(timezone.utc)
    session_offset = {s["id"]: s["started_offset_hours"] for s in _SESSIONS}
    for m in _MEASUREMENTS:
        if db.get(Measurement, m["id"]) is not None:
            continue
        offset_hours = session_offset[m["session_id"]] + m["minutes_after_start"] / 60.0
        measured_at = (now + timedelta(hours=offset_hours)).isoformat()
        measurement = Measurement(
            id=m["id"],
            session_id=m["session_id"],
            type=m["type"],
            value=m["value"],
            unit=m["unit"],
            source_device="seed",
            measured_at=measured_at,
            is_valid=1,
            validation_notes=None,
            raw_json=None,
            synced=0,
        )
        db.add(measurement)
        _record(
            db,
            object_type="measurement",
            object_id=measurement.id,
            details={
                "session_id": measurement.session_id,
                "type": measurement.type,
                "is_valid": measurement.is_valid,
            },
        )
        created += 1
    return created


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _print_summary(  # pragma: no cover
    report: SeedReport, totals: dict[str, int]
) -> None:
    # Pure stdout formatting reachable only from main(). Excluded from
    # coverage for the same reason main() is — testing the formatted
    # output would be brittle (string matching) and low value.
    print("=== GINHAWA dev-data seeder ===")
    print()
    print("Created in this run:")
    print(f"  Users:        {report.users_created}")
    print(f"  Citizens:     {report.citizens_created}")
    print(f"  Sessions:     {report.sessions_created}")
    print(f"  Measurements: {report.measurements_created}")
    print(f"  Audit rows:   {report.audit_rows_written}")
    print()
    print("Total in database after this run:")
    print(f"  Users:        {totals['users']}")
    print(f"  Citizens:     {totals['citizens']}")
    print(f"  Sessions:     {totals['sessions']}")
    print(f"  Measurements: {totals['measurements']}")
    print(f"  Audit rows:   {totals['audit']}")
    print()
    print("[CREDENTIALS — DEV ONLY, DO NOT USE IN PROD]")
    print(f"  admin            / {_ADMIN_PASSWORD}")
    print(f"  bhw_tibagan      / {_BHW_PASSWORD}")
    print(f"  bhw_pinaglabanan / {_BHW_PASSWORD}")
    print(f"  bhw_corazon      / {_BHW_PASSWORD}")
    print()
    print("[DEFERRED] Device credential not seeded — device_credentials table")
    print("does not exist yet. This will be added when the kiosk sync feature")
    print("(originally Phase 1 Prompt 9) is implemented. Tracked in ADR-XXXX.")


def main() -> int:  # pragma: no cover
    """CLI entry point. Connects to the production engine via SessionLocal."""
    from ..db.session import SessionLocal

    db = SessionLocal()
    try:
        report = seed(db)
        totals = {
            "users": _count(db, User),
            "citizens": _count(db, Citizen),
            "sessions": _count(db, SessionModel),
            "measurements": _count(db, Measurement),
            "audit": _count(db, AuditLog),
        }
        _print_summary(report, totals)
    finally:
        db.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
