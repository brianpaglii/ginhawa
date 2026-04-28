import pytest
from pydantic import ValidationError

from ginhawa_cloud.api.schemas import (
    CitizenCreate,
    MeasurementCreate,
    UserRead,
)


def test_citizen_create_accepts_valid_input() -> None:
    citizen = CitizenCreate(
        rfid_uid="04A1B2C3D4",  # pragma: allowlist secret
        full_name="Juan Dela Cruz",
        dob="1990-05-15",
        sex="M",
        barangay="Barangay 1",
        phone="+639171234567",
        consent_version="1.0",
    )
    assert citizen.full_name == "Juan Dela Cruz"
    assert citizen.sex == "M"
    assert citizen.dob == "1990-05-15"


def test_citizen_create_rejects_future_dob() -> None:
    with pytest.raises(ValidationError, match="dob must be in the past"):
        CitizenCreate(
            rfid_uid="04A1B2C3D4",  # pragma: allowlist secret
            full_name="Future Person",
            dob="2099-01-01",
            sex="M",
            barangay="Barangay 1",
            consent_version="1.0",
        )


def test_citizen_create_rejects_invalid_iso_dob() -> None:
    with pytest.raises(ValidationError, match="ISO 8601"):
        CitizenCreate(
            rfid_uid="04A1B2C3D4",  # pragma: allowlist secret
            full_name="Bad Date",
            dob="not-a-date",
            sex="M",
            barangay="Barangay 1",
            consent_version="1.0",
        )


def test_citizen_create_rejects_invalid_sex() -> None:
    with pytest.raises(ValidationError):
        CitizenCreate(
            rfid_uid="04A1B2C3D4",  # pragma: allowlist secret
            full_name="Test",
            dob="1990-01-01",
            sex="X",
            barangay="Barangay 1",
            consent_version="1.0",
        )


def test_measurement_create_accepts_valid_systolic() -> None:
    m = MeasurementCreate(
        session_id="00000000-0000-0000-0000-000000000001",
        type="systolic_bp",
        value=120.0,
        unit="mmHg",
        source_device="omron_hem7155t",
    )
    assert m.value == 120.0


def test_measurement_create_rejects_systolic_300() -> None:
    with pytest.raises(ValidationError, match="outside physiological range"):
        MeasurementCreate(
            session_id="00000000-0000-0000-0000-000000000001",
            type="systolic_bp",
            value=300.0,
            unit="mmHg",
            source_device="omron_hem7155t",
        )


def test_user_read_does_not_include_password_hash() -> None:
    fields = set(UserRead.model_fields)
    assert "password_hash" not in fields, (
        "UserRead must not expose password_hash; credentials never leave the DB"
    )
    assert "password" not in fields, (
        "UserRead must not expose plaintext password either"
    )


class _OrmUserStub:
    """Stands in for a SQLAlchemy User row carrying a password_hash attribute."""

    id = "00000000-0000-0000-0000-000000000001"
    username = "bhw_anna"
    password_hash = "argon2id$v=19$m=65536,t=3,p=4$secret"  # pragma: allowlist secret
    full_name = "Anna Reyes"
    role = "bhw"
    assigned_barangay = "Barangay 1"
    is_active = 1
    created_at = "2026-04-28T07:25:17Z"
    last_login_at = None


def test_user_read_serialization_strips_password_hash() -> None:
    user = UserRead.model_validate(_OrmUserStub())

    dumped = user.model_dump()
    assert "password_hash" not in dumped
    assert "password" not in dumped

    json_blob = user.model_dump_json()
    assert "password_hash" not in json_blob
    assert "argon2id" not in json_blob, (
        "the stub's password_hash must not leak through the JSON serializer"
    )


def test_user_read_drops_password_hash_passed_directly() -> None:
    user = UserRead.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000002",
            "username": "bhw_jose",
            "password_hash": "argon2id$leak$attempt",  # pragma: allowlist secret
            "full_name": "Jose Santos",
            "role": "bhw",
            "assigned_barangay": None,
            "is_active": 1,
            "created_at": "2026-04-28T07:25:17Z",
            "last_login_at": None,
        }
    )
    assert not hasattr(user, "password_hash")
    assert "password_hash" not in user.model_dump_json()
