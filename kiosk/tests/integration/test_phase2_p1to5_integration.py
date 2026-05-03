"""Phase 2 prompts 1–5 end-to-end integration test.

Verifies the layers compose correctly — schema, models, FSM,
validation, audit, sync daemon — without re-testing what unit tests
already cover.

Layers exercised:
* Schema + SQLCipher engine + Alembic-equivalent ``init_database``.
* SQLAlchemy ORM models (Citizen, Session, Measurement, AuditLog).
* :class:`SessionFSM` driven through every state in the happy thread.
* :class:`EventBus` carrying typed events from the test driver to
  the FSM.
* :func:`validate_measurement` stamping is_valid / validation_notes.
* :func:`record_audit` writing rows from both the FSM and the
  measurement handler.
* :class:`CloudClient` + :class:`SyncDaemon` posting batches to a
  pytest-httpx-mocked cloud and flipping local synced=1.

Hardware is not required — the test wires the event bus directly,
the cloud is a pytest-httpx mock, and SQLCipher uses an on-disk
file under ``tmp_path``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel
from pytest_httpx import HTTPXMock
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ginhawa_kiosk.db.models import AuditLog, Citizen, Measurement
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.db.session import (
    create_engine_for_kiosk,
    init_database,
    make_session_factory,
)
from ginhawa_kiosk.fsm import (
    EventBus,
    MeasurementProposed,
    PathSelected,
    RfidScanned,
    SessionFSM,
    State,
)
from ginhawa_kiosk.services.audit import record_audit
from ginhawa_kiosk.services.validation import validate_measurement
from ginhawa_kiosk.sync import CloudClient, SyncDaemon


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_KEY = "0" * 64  # pragma: allowlist secret
DEVICE_ID = "00000000-0000-0000-0000-000000000401"
API_KEY = "integration-test-api-key"  # pragma: allowlist secret
CLOUD_BASE_URL = "https://cloud.test.local"
CITIZEN_ID = "00000000-0000-0000-0000-000000000101"
CONSENT_VERSION = "1.0"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Test-local event types
#
# ``MeasurementProposed`` is the production event imported above.
# The remaining test-local event classes are thin "the citizen
# pressed the button" markers; they exist purely so the wiring
# driver has a single subscribe point per UI action without
# inflating the production event bus surface for events that have
# no payload.
# ---------------------------------------------------------------------------


class MeasurementPathCompleteEvent(BaseModel):
    pass


class FinishWithoutPrintingEvent(BaseModel):
    pass


class AcknowledgeEvent(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Wiring — bus events → FSM triggers + DB writes
# ---------------------------------------------------------------------------


class IntegrationDriver:
    """Subscribes to the bus and translates events into FSM triggers
    + DB mutations. Mirrors the wiring the production GUI / sensor
    adapter layer will eventually own.
    """

    def __init__(self, *, fsm: SessionFSM, db: Session, bus: EventBus) -> None:
        self.fsm = fsm
        self.db = db
        self.bus = bus
        self.last_validation_notes: str | None = None
        self._wire()

    def _wire(self) -> None:
        self.bus.subscribe(RfidScanned, self._on_rfid_scanned)
        self.bus.subscribe(PathSelected, self._on_path_selected)
        self.bus.subscribe(MeasurementProposed, self._on_measurement_proposed)
        self.bus.subscribe(
            MeasurementPathCompleteEvent, self._on_measurement_path_complete
        )
        self.bus.subscribe(FinishWithoutPrintingEvent, self._on_finish_without_printing)
        self.bus.subscribe(AcknowledgeEvent, self._on_acknowledge)

    async def _on_rfid_scanned(self, event: RfidScanned) -> None:
        self.fsm.rfid_scanned(event.uid)
        # Lookup runs as part of the wiring layer (in production this
        # is the citizen-lookup service). On miss, citizen=None drives
        # the REGISTERING branch.
        citizen = self.db.execute(
            select(Citizen).where(Citizen.rfid_uid == event.uid)
        ).scalar_one_or_none()
        # Audit the read explicitly — _ensure_session_row only fires
        # later. This mirrors how the production lookup service will
        # log every RFID resolution attempt.
        record_audit(
            self.db,
            actor_type="kiosk",
            actor_id=DEVICE_ID,
            action="citizen.read",
            object_type="citizen",
            object_id=citizen.id if citizen else None,
            details={"rfid_uid": event.uid, "found": citizen is not None},
        )
        self.fsm.citizen_identified(citizen)
        # The Phase 2 p1-5 integration test predates the LANGUAGE_SELECT
        # state added in prompt 8; default to English so the rest of the
        # thread (CONSENT / PATH_CHOICE / measuring / report) still
        # exercises the same code paths.
        self.fsm.language_chosen("en")

    async def _on_path_selected(self, event: PathSelected) -> None:
        self.fsm.path_selected(event.path)

    async def _on_measurement_proposed(self, event: MeasurementProposed) -> None:
        if self.fsm.current_session is None:
            raise RuntimeError(
                "MeasurementProposed received with no current session — "
                "wiring bug in the test"
            )
        # The validation service has the final say on is_valid; the
        # kiosk's prior belief (``event.claimed_is_valid``) is
        # overridden if range / unit checks disagree.
        result = validate_measurement(event.measurement_type, event.value, event.unit)
        self.last_validation_notes = result.validation_notes

        now = _utc_now_iso()
        measurement = Measurement(
            id=str(uuid.uuid4()),
            session_id=self.fsm.current_session.id,
            type=event.measurement_type,
            value=event.value,
            unit=event.unit,
            source_device=event.source_device,
            measured_at=now,
            is_valid=1 if result.is_valid else 0,
            validation_notes=result.validation_notes,
            raw_json=None,
            synced=0,
            updated_at=now,
        )
        self.db.add(measurement)
        self.db.flush()
        # Notify the FSM (writes its own audit row for the capture).
        self.fsm.measurement_captured(measurement.id)
        # Persist the FSM's audit row + the measurement row in one
        # transaction.
        self.db.commit()

    async def _on_measurement_path_complete(
        self, _: MeasurementPathCompleteEvent
    ) -> None:
        self.fsm.measurement_path_complete()
        self.db.commit()

    async def _on_finish_without_printing(self, _: FinishWithoutPrintingEvent) -> None:
        self.fsm.finish_without_printing()
        self.db.commit()

    async def _on_acknowledge(self, _: AcknowledgeEvent) -> None:
        self.fsm.acknowledge()
        self.db.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine_for_kiosk(tmp_path / "integration.db", DB_KEY)
    init_database(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def citizen(db_session: Session) -> Citizen:
    now = _utc_now_iso()
    c = Citizen(
        id=CITIZEN_ID,
        rfid_uid="INTEGRATION_TEST_001",
        full_name="Integration Probe",
        dob="1980-01-01",
        sex="F",
        barangay="Tibagan",
        phone=None,
        consent_version=CONSENT_VERSION,
        consent_given_at=now,
        registered_at=now,
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at=now,
    )
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def fsm(db_session: Session) -> SessionFSM:
    return SessionFSM(
        db_session,
        device_id=DEVICE_ID,
        current_consent_version=CONSENT_VERSION,
    )


@pytest.fixture
def bus() -> EventBus:
    # No logger argument — let the bus use the default structlog
    # logger; the test asserts on DB state, not log output.
    return EventBus()


@pytest.fixture
def driver(fsm: SessionFSM, db_session: Session, bus: EventBus) -> IntegrationDriver:
    return IntegrationDriver(fsm=fsm, db=db_session, bus=bus)


@pytest_asyncio.fixture
async def cloud_client() -> AsyncIterator[CloudClient]:
    client = CloudClient(base_url=CLOUD_BASE_URL, api_key=API_KEY, device_id=DEVICE_ID)
    try:
        yield client
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# Verifies the full happy thread end-to-end: FSM transitions, DB
# rows persist, audit log captures every transition + every
# measurement, and the sync daemon flips synced=1 in FK order against
# the mocked cloud.
@pytest.mark.asyncio
async def test_full_session_with_sync_daemon(
    bus: EventBus,
    driver: IntegrationDriver,
    fsm: SessionFSM,
    db_session: Session,
    session_factory: sessionmaker[Session],
    cloud_client: CloudClient,
    citizen: Citizen,
    httpx_mock: HTTPXMock,
) -> None:
    # 1. Drive the FSM through the full thread ----------------------
    assert fsm.state == State.IDLE

    await bus.publish(RfidScanned(uid="INTEGRATION_TEST_001"))
    assert fsm.state == State.PATH_CHOICE, (
        f"expected PATH_CHOICE after rfid scan + lookup + language; got {fsm.state}"
    )
    assert fsm.current_session is not None

    await bus.publish(PathSelected(path="full"))
    assert fsm.state == State.MEASURING_VITALS

    vitals = [
        ("systolic_bp", 128.0, "mmHg", "mock_omron"),
        ("diastolic_bp", 82.0, "mmHg", "mock_omron"),
        ("spo2", 98.0, "%", "mock_max30100"),
        ("heart_rate", 72.0, "bpm", "mock_max30100"),
        ("temperature", 36.5, "C", "mock_mlx90640"),
    ]
    for t, v, u, src in vitals:
        await bus.publish(
            MeasurementProposed(
                measurement_type=t,
                value=v,
                unit=u,
                source_device=src,
                claimed_is_valid=True,
            )
        )

    await bus.publish(MeasurementPathCompleteEvent())
    assert fsm.state == State.MEASURING_ANTHRO

    anthro = [
        ("height", 165.0, "cm", "mock_vl53l0x"),
        ("weight", 65.0, "kg", "mock_xiaomi"),
        ("bmi", 23.9, "", "derived"),
    ]
    for t, v, u, src in anthro:
        await bus.publish(
            MeasurementProposed(
                measurement_type=t,
                value=v,
                unit=u,
                source_device=src,
                claimed_is_valid=True,
            )
        )

    await bus.publish(MeasurementPathCompleteEvent())
    assert fsm.state == State.REPORT

    await bus.publish(FinishWithoutPrintingEvent())
    assert fsm.state == State.END

    session_id = fsm.current_session.id  # capture before acknowledge wipes it

    await bus.publish(AcknowledgeEvent())
    assert fsm.state == State.IDLE
    assert fsm.current_session is None

    # 2. Assertions on local DB state -------------------------------
    db_session.expire_all()

    sessions = (
        db_session.execute(
            select(SessionModel).where(SessionModel.citizen_id == citizen.id)
        )
        .scalars()
        .all()
    )
    assert len(sessions) == 1, f"expected 1 session, got {len(sessions)}"
    s = sessions[0]
    assert s.status == "completed", f"status was {s.status!r}"
    assert s.ended_at is not None, "ended_at was not set on END"
    assert s.printed_status == "not_requested"
    assert s.measurement_path == "full"
    assert s.synced == 0

    measurements = (
        db_session.execute(select(Measurement).where(Measurement.session_id == s.id))
        .scalars()
        .all()
    )
    assert len(measurements) == 8, (
        f"expected 8 measurements (5 vitals + 3 anthro), got "
        f"{len(measurements)}; types={[m.type for m in measurements]}"
    )

    expected_payloads = {
        "systolic_bp": (128.0, "mmHg"),
        "diastolic_bp": (82.0, "mmHg"),
        "spo2": (98.0, "%"),
        "heart_rate": (72.0, "bpm"),
        "temperature": (36.5, "C"),
        "height": (165.0, "cm"),
        "weight": (65.0, "kg"),
        "bmi": (23.9, ""),
    }
    for m in measurements:
        assert m.is_valid == 1, (
            f"measurement {m.type} should be valid (in range); got "
            f"is_valid=0, notes={m.validation_notes!r}"
        )
        assert m.synced == 0
        expected_value, expected_unit = expected_payloads[m.type]
        assert m.value == pytest.approx(expected_value)
        assert m.unit == expected_unit

    # 3. Audit assertions -------------------------------------------
    audits = db_session.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
    actor_types = {a.actor_type for a in audits}
    actions = [a.action for a in audits]

    # Must have at least: citizen.read + every FSM transition action +
    # every measurement_captured action.
    assert "citizen.read" in actions, (
        f"expected citizen.read audit row; saw actions={actions}"
    )
    assert "fsm.rfid_scanned" in actions
    assert "fsm.session_started" in actions
    assert "fsm.language_chosen" in actions
    assert "fsm.path_selected" in actions
    assert actions.count("fsm.measurement_captured") == 8, (
        f"expected 8 measurement_captured audits, got "
        f"{actions.count('fsm.measurement_captured')}"
    )
    assert "fsm.measurement_path_step" in actions  # full → vitals→anthro
    assert "fsm.report" in actions
    assert "fsm.finish_without_printing" in actions
    assert "fsm.acknowledge" in actions

    # Mix of citizen / system attribution; never admin.
    assert "citizen" in actor_types, (
        f"no citizen-attributed audit rows? actor_types={actor_types}"
    )
    assert "system" in actor_types, (
        f"no system-attributed audit rows? actor_types={actor_types}"
    )
    assert "admin" not in actor_types, (
        f"admin actor_type should never appear on the kiosk; actor_types={actor_types}"
    )

    # 4. Run sync daemon against mocked cloud -----------------------
    httpx_mock.add_response(
        method="POST",
        url=f"{CLOUD_BASE_URL}/api/v1/sync/citizens",
        json={"results": [{"id": citizen.id, "status": "created", "error": None}]},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CLOUD_BASE_URL}/api/v1/sync/sessions",
        json={"results": [{"id": session_id, "status": "created", "error": None}]},
        status_code=200,
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{CLOUD_BASE_URL}/api/v1/sync/measurements",
        json={
            "results": [
                {"id": m.id, "status": "created", "error": None} for m in measurements
            ]
        },
        status_code=200,
    )

    daemon = SyncDaemon(
        session_factory=session_factory,
        cloud=cloud_client,
        interval_seconds=30.0,
    )
    await daemon.run_once()

    # FK-safe ordering of the daemon's POSTs.
    paths = [r.url.path for r in httpx_mock.get_requests()]
    assert paths == [
        "/api/v1/sync/citizens",
        "/api/v1/sync/sessions",
        "/api/v1/sync/measurements",
    ], f"unexpected request order: {paths}"

    # All previously-unsynced rows now synced=1.
    db_session.expire_all()
    assert db_session.get(Citizen, citizen.id).synced == 1
    assert db_session.get(SessionModel, session_id).synced == 1
    measurements_after = (
        db_session.execute(
            select(Measurement).where(Measurement.session_id == session_id)
        )
        .scalars()
        .all()
    )
    assert all(m.synced == 1 for m in measurements_after), (
        f"some measurements unsynced after daemon run: "
        f"{[(m.type, m.synced) for m in measurements_after]}"
    )


# Verifies the negative path: an out-of-range systolic_bp during a
# fresh session lands on the Measurement row with is_valid=0 and a
# validation_notes string that contains 'outside physiological range'.
# Confirms the validation service ran (the driver records the
# validate_measurement return so the test asserts on it directly,
# not just on the persisted shape).
@pytest.mark.asyncio
async def test_out_of_range_measurement_marked_invalid(
    bus: EventBus,
    driver: IntegrationDriver,
    fsm: SessionFSM,
    db_session: Session,
    citizen: Citizen,
) -> None:
    await bus.publish(RfidScanned(uid="INTEGRATION_TEST_001"))
    assert fsm.state == State.PATH_CHOICE

    await bus.publish(PathSelected(path="vitals"))
    assert fsm.state == State.MEASURING_VITALS

    await bus.publish(
        MeasurementProposed(
            measurement_type="systolic_bp",
            value=300.0,
            unit="mmHg",
            source_device="mock_omron",
            # kiosk *thought* it was valid; the validation service
            # has the final say and must override.
            claimed_is_valid=True,
        )
    )

    db_session.expire_all()
    rows = (
        db_session.execute(
            select(Measurement).where(
                Measurement.session_id == fsm.current_session.id,
                Measurement.type == "systolic_bp",
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.is_valid == 0, (
        f"expected is_valid=0 for out-of-range systolic; got "
        f"is_valid={row.is_valid}, notes={row.validation_notes!r}"
    )
    assert row.validation_notes is not None
    assert "outside physiological range" in row.validation_notes
    assert "300.0" in row.validation_notes

    # Confirm validation_service ran via the driver's record of the
    # most recent return value.
    assert driver.last_validation_notes is not None
    assert "outside physiological range" in driver.last_validation_notes

    # Sanity: total measurement count for this session is exactly 1
    # (the rejected-but-stored row), and no audit row for the kiosk
    # mentions a synced=1 marker (we haven't synced).
    total = db_session.execute(
        select(func.count(Measurement.id)).where(
            Measurement.session_id == fsm.current_session.id
        )
    ).scalar_one()
    assert total == 1
    assert row.synced == 0
