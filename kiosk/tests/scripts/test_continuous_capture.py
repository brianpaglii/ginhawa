"""Continuous-capture diagnostic CLI — unit tests on the mock-sensor path.

All tests run with ``MOCK_HARDWARE=true`` so they exercise the
factory and bus wiring on any machine. Real-hardware verification of
the same wiring is documented in
``kiosk/docs/verification/2026-05-02-phase2-p6-sensors-bench.md``.
"""

from __future__ import annotations

import asyncio
import io
import json
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ginhawa_kiosk.core.config import Settings
from ginhawa_kiosk.scripts import continuous_capture as cc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "capture-test.db"


@pytest.fixture
def settings(tmp_db_path: Path) -> Iterator[Settings]:
    """A real ``Settings`` configured for mock hardware so sensor
    factories pick the in-memory implementations. Bypasses the
    ``get_settings`` lru_cache by constructing directly."""
    s = Settings(
        KIOSK_DB_PATH=tmp_db_path,
        KIOSK_DB_KEY="0" * 64,  # pragma: allowlist secret
        KIOSK_API_KEY="cc-test-api-key",  # pragma: allowlist secret
        KIOSK_DEVICE_ID="00000000-0000-0000-0000-000000000401",
        MOCK_HARDWARE=True,
    )
    yield s


@pytest.fixture(autouse=True)
def _stub_engine_and_factory(mocker: Any) -> None:
    """Patch the SQLCipher engine + session factory at the cc module
    boundary so unit tests don't depend on libsqlcipher being
    installed. The continuous-capture tool itself only uses the
    session as a constructor arg for the mock sensors (which ignore
    it); a MagicMock is sufficient.

    The real-hardware path on a Pi opens a real encrypted DB; that
    coverage lives in the bench-test verification document."""
    fake_engine = mocker.MagicMock(name="FakeEngine")

    def _factory_call(*_a: Any, **_kw: Any) -> Any:
        # Each call returns a fresh MagicMock to mimic sessionmaker()
        return mocker.MagicMock(name="FakeSession")

    fake_factory = mocker.MagicMock(
        name="FakeSessionFactory", side_effect=_factory_call
    )
    mocker.patch.object(cc, "create_engine_for_kiosk", return_value=fake_engine)
    mocker.patch.object(cc, "make_session_factory", return_value=fake_factory)


@pytest.fixture
def stdout_buffer() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def args_all(tmp_path: Path) -> cc._Args:
    return cc._Args(
        sensors="all",
        log_file=tmp_path / "capture.jsonl",
        verbose=False,
        bp_prompt=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_with_injection(
    args: cc._Args,
    settings: Settings,
    stdout: io.StringIO,
    stop_after: float,
    on_started: Any | None = None,
) -> int:
    """Drive ``cc._run`` and optionally inject sensor events after a
    short warm-up. ``on_started`` runs once start-up has had a moment
    to subscribe handlers."""

    async def _runner() -> int:
        return await cc._run(
            args, settings=settings, stdout=stdout, stop_after_seconds=stop_after
        )

    if on_started is None:
        return await _runner()

    runner_task = asyncio.create_task(_runner())
    # Yield once so the event loop can hit the asyncio.sleep inside _run
    # and the sensors get to start() before we trigger their mocks.
    await asyncio.sleep(0)
    await on_started()
    return await runner_task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# Verifies the tool subscribes to RfidScanned and prints a stdout
# line carrying the UID. Drives a mock RFID tap on the sensor the
# tool just instantiated.
# Mortality: would fail if event subscription were dropped.
@pytest.mark.asyncio
async def test_continuous_capture_prints_rfid_event(
    args_all: cc._Args,
    settings: Settings,
    stdout_buffer: io.StringIO,
    mocker: Any,
) -> None:
    # Capture the RFID sensor instance the script builds, so the test
    # can call simulate_tap on it.
    captured: dict[str, Any] = {}
    real_create = cc.create_rfid_reader

    def _spy(bus: Any, settings_arg: Any) -> Any:
        sensor = real_create(bus, settings_arg)
        captured["rfid"] = sensor
        return sensor

    mocker.patch.object(cc, "create_rfid_reader", side_effect=_spy)

    async def trigger() -> None:
        await asyncio.sleep(0.05)  # let the sensor start
        await captured["rfid"].simulate_tap("a3f2c901")
        await asyncio.sleep(0.05)  # let the bus dispatch

    rc = await _run_with_injection(
        args_all,
        settings,
        stdout_buffer,
        stop_after=0.2,
        on_started=trigger,
    )
    assert rc == 0
    out = stdout_buffer.getvalue()
    assert "RfidScanned: uid=A3F2C901" in out


# Verifies a single mock BP measurement produces three
# MeasurementProposed lines (systolic, diastolic, heart_rate). This
# is the exact bug-shape we hit during Phase 2 Prompt 6 bench
# testing where one publish call was accidentally dropped.
# Mortality: would fail if any of the three publish handlers were
# skipped.
@pytest.mark.asyncio
async def test_continuous_capture_prints_three_events_per_bp_measurement(
    args_all: cc._Args,
    settings: Settings,
    stdout_buffer: io.StringIO,
    mocker: Any,
) -> None:
    captured: dict[str, Any] = {}
    real_create = cc.create_omron_bp

    def _spy(bus: Any, settings_arg: Any, db: Any) -> Any:
        sensor = real_create(bus, settings_arg, db)
        captured["omron"] = sensor
        return sensor

    mocker.patch.object(cc, "create_omron_bp", side_effect=_spy)

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        await captured["omron"].simulate_measurement(
            systolic=128.0, diastolic=82.0, pulse=74.0
        )
        await asyncio.sleep(0.05)

    rc = await _run_with_injection(
        args_all,
        settings,
        stdout_buffer,
        stop_after=0.2,
        on_started=trigger,
    )
    assert rc == 0
    out = stdout_buffer.getvalue()
    # Filter for the actual event lines (containing "type=") rather
    # than the summary footer's "  MeasurementProposed: <count>" line.
    measurement_lines = [
        line
        for line in out.splitlines()
        if "MeasurementProposed:" in line and "type=" in line
    ]
    assert len(measurement_lines) == 3, (
        f"expected 3 MeasurementProposed lines (systolic/diastolic/"
        f"heart_rate), got {len(measurement_lines)}: {measurement_lines}"
    )
    types = sorted(
        line.split("type=", 1)[1].split(" ", 1)[0] for line in measurement_lines
    )
    assert types == ["diastolic_bp", "heart_rate", "systolic_bp"]


# Verifies --log-file produces a JSONL file with one well-formed
# JSON object per event. The file path is a tmp_path so the test
# is isolated from the operator's home directory.
# Mortality: would fail if file logging were broken or events were
# not serialized correctly.
@pytest.mark.asyncio
async def test_continuous_capture_writes_jsonl_log(
    settings: Settings,
    stdout_buffer: io.StringIO,
    tmp_path: Path,
    mocker: Any,
) -> None:
    log_file = tmp_path / "capture-jsonl-test.jsonl"
    args = cc._Args(
        sensors="rfid",
        log_file=log_file,
        verbose=False,
        bp_prompt=False,
    )

    captured: dict[str, Any] = {}
    real_create = cc.create_rfid_reader

    def _spy(bus: Any, settings_arg: Any) -> Any:
        sensor = real_create(bus, settings_arg)
        captured["rfid"] = sensor
        return sensor

    mocker.patch.object(cc, "create_rfid_reader", side_effect=_spy)

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        await captured["rfid"].simulate_tap("DEADBEEF")
        await asyncio.sleep(0.05)

    rc = await _run_with_injection(
        args, settings, stdout_buffer, stop_after=0.2, on_started=trigger
    )
    assert rc == 0
    assert log_file.exists()

    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    # Exactly one event was simulated; pre-existing log file lines
    # don't matter because the path was a fresh tmp_path.
    assert len(lines) == 1, f"expected 1 JSON line, got {len(lines)}: {lines}"
    record = json.loads(lines[0])
    assert record["event"] == "RfidScanned"
    assert record["uid"] == "DEADBEEF"
    assert "timestamp" in record


# Verifies the tool never writes to the citizen / sessions /
# measurements / audit_log tables. We intercept the SQLAlchemy
# Session's add / merge / commit on the actual session that the
# script opens, and assert zero calls.
# Mortality: would fail if the tool were ever modified to persist
# events — which would be a deliberate decision belonging in a
# separate prompt.
@pytest.mark.asyncio
async def test_continuous_capture_does_not_write_to_database(
    args_all: cc._Args,
    settings: Settings,
    stdout_buffer: io.StringIO,
    mocker: Any,
) -> None:
    # Capture every session the script opens so we can inspect
    # mutation calls afterwards. The autouse fixture's factory
    # returns MagicMocks; we record those instances and verify none
    # had add / merge / commit / delete invoked.
    opened_sessions: list[Any] = []

    fake_engine = mocker.MagicMock(name="FakeEngine")
    mocker.patch.object(cc, "create_engine_for_kiosk", return_value=fake_engine)

    def _capture_session_factory(*_a: Any, **_kw: Any) -> Any:
        def _open_session() -> Any:
            session = mocker.MagicMock(name="WatchedSession")
            opened_sessions.append(session)
            return session

        factory = mocker.MagicMock(name="WatchingFactory", side_effect=_open_session)
        return factory

    mocker.patch.object(
        cc, "make_session_factory", side_effect=_capture_session_factory
    )

    captured: dict[str, Any] = {}
    real_create = cc.create_omron_bp

    def _spy(bus: Any, settings_arg: Any, db: Any) -> Any:
        sensor = real_create(bus, settings_arg, db)
        captured["omron"] = sensor
        return sensor

    mocker.patch.object(cc, "create_omron_bp", side_effect=_spy)

    async def trigger() -> None:
        await asyncio.sleep(0.05)
        await captured["omron"].simulate_measurement(
            systolic=120.0, diastolic=80.0, pulse=72.0
        )
        await asyncio.sleep(0.05)

    rc = await _run_with_injection(
        args_all, settings, stdout_buffer, stop_after=0.2, on_started=trigger
    )
    assert rc == 0
    assert opened_sessions, "expected the script to open at least one DB session"
    for session in opened_sessions:
        assert session.add.call_count == 0, "continuous-capture called Session.add"
        assert session.merge.call_count == 0, "continuous-capture called Session.merge"
        assert session.commit.call_count == 0, (
            "continuous-capture called Session.commit"
        )
        assert session.delete.call_count == 0, (
            "continuous-capture called Session.delete"
        )


# Verifies the CLI exits cleanly on SIGINT and prints the summary.
# This runs the actual CLI as a subprocess (the only way to
# faithfully drive the signal-handling path) under MOCK_HARDWARE so
# no real BLE / SPI is touched.
# Mortality: would fail if Ctrl-C were not handled cleanly,
# leaving the operator's terminal in a bad state.
def test_continuous_capture_handles_keyboard_interrupt(
    tmp_path: Path,
) -> None:
    # This test runs the actual CLI as a subprocess so it exercises
    # signal-handling for real. The subprocess opens the SQLCipher
    # engine at startup, so we need libsqlcipher to be importable.
    # On dev laptops without it (e.g., CachyOS without the system
    # sqlcipher package), skip.
    pytest.importorskip(
        "sqlcipher3",
        reason=(
            "system SQLCipher not available; the subprocess SIGINT "
            "test needs to open a real encrypted DB. Run on a Pi or "
            "install libsqlcipher to exercise this path."
        ),
    )

    db_path = tmp_path / "sigint.db"
    log_path = tmp_path / "sigint.jsonl"
    # Provision the DB once so the subprocess can open it.
    from ginhawa_kiosk.db.session import (
        create_engine_for_kiosk,
        init_database,
    )

    engine = create_engine_for_kiosk(db_path, "0" * 64)  # pragma: allowlist secret
    init_database(engine)
    engine.dispose()

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ginhawa_kiosk.scripts.continuous_capture",
            "--sensors",
            "rfid",
            "--log-file",
            str(log_path),
            "--no-bp-prompt",
        ],
        env={
            "PATH": "/usr/bin:/bin",
            "KIOSK_DB_PATH": str(db_path),
            "KIOSK_DB_KEY": "0" * 64,  # pragma: allowlist secret
            "KIOSK_API_KEY": "cc-test-api-key",  # pragma: allowlist secret
            "KIOSK_DEVICE_ID": "00000000-0000-0000-0000-000000000401",
            "MOCK_HARDWARE": "true",
            # uv run wrapper is unnecessary here — Python's already in
            # the project venv from pytest's POV.
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Let the process boot.
    try:
        # Give it a moment to start sensors and subscribe handlers.
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass  # expected; the loop is running
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=5.0)
    assert proc.returncode == 0, (
        f"expected exit 0 on SIGINT; got {proc.returncode}.\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert "capture summary" in out, f"expected summary in stdout; got:\n{out}"


# Verifies --sensors rfid only instantiates the RFID factory; the
# Xiaomi and Omron factories are NOT called.
# Mortality: would fail if the --sensors flag were ignored.
@pytest.mark.asyncio
async def test_continuous_capture_only_starts_requested_sensors(
    settings: Settings,
    stdout_buffer: io.StringIO,
    tmp_path: Path,
    mocker: Any,
) -> None:
    args = cc._Args(
        sensors="rfid",
        log_file=tmp_path / "rfid-only.jsonl",
        verbose=False,
        bp_prompt=False,
    )
    rfid_spy = mocker.spy(cc, "create_rfid_reader")
    xiaomi_spy = mocker.spy(cc, "create_xiaomi_scale")
    omron_spy = mocker.spy(cc, "create_omron_bp")

    rc = await cc._run(
        args, settings=settings, stdout=stdout_buffer, stop_after_seconds=0.05
    )
    assert rc == 0
    assert rfid_spy.call_count == 1
    assert xiaomi_spy.call_count == 0
    assert omron_spy.call_count == 0
