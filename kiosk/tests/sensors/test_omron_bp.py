"""Omron BP cuff sensor: SIG payload parsing + mock event publishing."""

from __future__ import annotations

from typing import Any

import pytest

from ginhawa_kiosk.fsm import EventBus, MeasurementProposed
from ginhawa_kiosk.sensors.omron_bp import (
    BpReading,
    MockOmronBp,
    OmronBpSensor,
    parse_bp_measurement,
    parse_sfloat16,
)


# Verifies SFLOAT-16 decode: small positive integers (mantissa with
# zero exponent) and one negative exponent case (10.0 = 100 * 10^-1).
# Mortality: would fail if the exponent or mantissa sign-extension
# were broken.
def test_parse_sfloat16_basic_cases() -> None:
    # 120 = mantissa 0x078, exponent 0 → raw 0x0078 → bytes (0x78, 0x00)
    assert parse_sfloat16(0x78, 0x00) == pytest.approx(120.0)
    # 80 = 0x050 → (0x50, 0x00)
    assert parse_sfloat16(0x50, 0x00) == pytest.approx(80.0)
    # 10.0 = mantissa 100 (0x064), exponent -1 (high nibble 0xF) →
    # raw 0xF064 → bytes (0x64, 0xF0). 100 * 10^-1 = 10.0
    assert parse_sfloat16(0x64, 0xF0) == pytest.approx(10.0)


# Verifies the SIG Blood Pressure Measurement parser extracts
# systolic / diastolic / MAP / pulse from a hardcoded payload that
# represents a normal reading. The flags byte sets bit 2 (pulse-rate
# present) but no time-stamp; the parser must skip the time-stamp
# field correctly.
# Mortality: would fail if the SFLOAT-16 parsing were broken or the
# optional-field offsets miscalculated.
def test_omron_bp_parses_sig_blood_pressure_measurement() -> None:
    payload = bytes(
        [
            0x04,  # flags: pulse-rate present, no time-stamp
            0x78,
            0x00,  # systolic = 120
            0x50,
            0x00,  # diastolic = 80
            0x5D,
            0x00,  # MAP = 93
            0x48,
            0x00,  # pulse = 72
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.systolic_mmhg == pytest.approx(120.0)
    assert reading.diastolic_mmhg == pytest.approx(80.0)
    assert reading.map_mmhg == pytest.approx(93.0)
    assert reading.pulse_bpm == pytest.approx(72.0)


# Verifies the parser correctly skips a 7-byte time-stamp field when
# flags bit 1 is set, then reads pulse from the right offset.
def test_omron_bp_parser_skips_optional_timestamp() -> None:
    payload = bytes(
        [
            0x06,  # flags: time-stamp + pulse-rate present
            0x78,
            0x00,  # systolic
            0x50,
            0x00,  # diastolic
            0x5D,
            0x00,  # MAP
            0xE8,
            0x07,  # year LSB-MSB = 2024 (0x07E8)
            0x05,  # month
            0x02,  # day
            0x0E,  # hour
            0x1E,  # minute
            0x00,  # second
            0x4B,
            0x00,  # pulse = 75
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.pulse_bpm == pytest.approx(75.0)


# Verifies the parser raises a clear error on a payload that's
# shorter than the 7-byte minimum (flags + 3 SFLOAT-16 fields).
def test_omron_bp_parser_rejects_truncated_payload() -> None:
    with pytest.raises(ValueError, match="too short"):
        parse_bp_measurement(bytes([0x04, 0x78, 0x00]))


# Verifies pulse_bpm is None when flags bit 2 is not set (no pulse
# field present). The cuff doesn't always report pulse.
def test_omron_bp_parser_returns_none_pulse_when_absent() -> None:
    payload = bytes(
        [
            0x00,  # flags: nothing optional
            0x78,
            0x00,
            0x50,
            0x00,
            0x5D,
            0x00,
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.pulse_bpm is None


# Verifies the parser raises when flags claim a pulse field but the
# payload is too short to contain one. Exercises the pulse-rate
# truncation guard (a separate branch from the minimum-length check).
def test_omron_bp_parser_rejects_truncated_pulse_field() -> None:
    payload = bytes(
        [
            0x04,  # pulse-rate present
            0x78,
            0x00,
            0x50,
            0x00,
            0x5D,
            0x00,
            # No pulse bytes follow.
        ]
    )
    with pytest.raises(ValueError, match="pulse-rate but is truncated"):
        parse_bp_measurement(payload)


# Verifies the mock's lifecycle. is_running flips correctly across
# start/stop and survives a no-pulse simulate_measurement call.
@pytest.mark.asyncio
async def test_mock_omron_bp_lifecycle(bus: EventBus) -> None:
    sensor = MockOmronBp(bus)
    assert sensor.is_running is False
    await sensor.start()
    assert sensor.is_running is True
    await sensor.stop()
    assert sensor.is_running is False


# Verifies the mock publishes one MeasurementProposed event per
# component (systolic / diastolic / pulse).
@pytest.mark.asyncio
async def test_mock_omron_bp_publishes_events(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    sensor = MockOmronBp(bus)
    await sensor.simulate_measurement(systolic=120, diastolic=80, pulse=72)

    types = [m.measurement_type for m in captured_measurements]
    assert types == ["systolic_bp", "diastolic_bp", "heart_rate"]
    by_type = {m.measurement_type: m for m in captured_measurements}
    assert by_type["systolic_bp"].value == pytest.approx(120)
    assert by_type["diastolic_bp"].value == pytest.approx(80)
    assert by_type["heart_rate"].value == pytest.approx(72)


# Verifies pulse is omitted from the published events when None.
@pytest.mark.asyncio
async def test_mock_omron_bp_omits_pulse_when_absent(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    sensor = MockOmronBp(bus)
    await sensor.simulate_measurement(systolic=120, diastolic=80)
    types = {m.measurement_type for m in captured_measurements}
    assert types == {"systolic_bp", "diastolic_bp"}


# Verifies BpReading is the right shape — small sanity test for the
# dataclass that downstream parsing depends on.
def test_bp_reading_dataclass_carries_all_fields() -> None:
    r = BpReading(systolic_mmhg=120, diastolic_mmhg=80, map_mmhg=93, pulse_bpm=72)
    assert r.systolic_mmhg == 120
    assert r.diastolic_mmhg == 80
    assert r.map_mmhg == 93
    assert r.pulse_bpm == 72


# Verifies SFLOAT-16 sign-extends a negative mantissa correctly.
# A mantissa of 0xFFE (= -2 after sign extension) with exponent 0
# should decode to -2.0. None of the BP path readings hit this in
# practice, but the parser must be correct end-to-end.
def test_parse_sfloat16_negative_mantissa() -> None:
    assert parse_sfloat16(0xFE, 0x0F) == pytest.approx(-2.0)


# Verifies OmronBpSensor's __init__ + is_running surface, and that
# _publish_reading routes a BpReading into three MeasurementProposed
# events (or two when pulse is None). Bypasses BLE entirely.
@pytest.mark.asyncio
async def test_omron_bp_sensor_publish_reading(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    # _publish_reading doesn't touch the database; a mock session
    # is plenty and keeps the test independent of SQLCipher's system
    # library being installed.
    db_session = mocker.MagicMock(name="DbSession")
    sensor = OmronBpSensor(bus, db_session)
    assert sensor.is_running is False

    await sensor._publish_reading(
        BpReading(systolic_mmhg=120, diastolic_mmhg=80, map_mmhg=93, pulse_bpm=72)
    )
    types = [m.measurement_type for m in captured_measurements]
    assert types == ["systolic_bp", "diastolic_bp", "heart_rate"]

    # Without pulse: only two events, no heart_rate.
    captured_measurements.clear()
    await sensor._publish_reading(
        BpReading(systolic_mmhg=118, diastolic_mmhg=78, map_mmhg=91, pulse_bpm=None)
    )
    types = {m.measurement_type for m in captured_measurements}
    assert types == {"systolic_bp", "diastolic_bp"}


# ---------------------------------------------------------------------------
# Real-hardware-path tests — plain bleak.BleakClient(mac).connect()
# ---------------------------------------------------------------------------
#
# Pattern lifted from userx14/omblepy: pass the MAC straight to
# BleakClient and let BlueZ's known-device cache resolve it. No
# pre-scan, no bleak-retry-connector. Tests patch BleakClient itself
# so we can verify the connect → start_notify → stop_notify →
# disconnect handshake without hardware.


# Verifies the real path passes the configured MAC string directly to
# BleakClient(mac), then awaits client.connect() — no separate scan,
# no BLEDevice resolution step. Mirrors omblepy's connection pattern
# and is the result of the 2026-05-02 bench finding that
# bleak-retry-connector's BLEDevice requirement adds a 20 s scan
# window we don't actually need on a pre-paired device.
# Mortality: would fail if the real path went back to scanning first
# or to passing a wrapped object to BleakClient.
@pytest.mark.asyncio
async def test_omron_bp_passes_mac_directly_to_bleak_client(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    payload = bytes([0x04, 0x78, 0x00, 0x50, 0x00, 0x5D, 0x00, 0x48, 0x00])
    mock_client = _make_mock_connected_client(mocker, payload)
    bleak_client_class = mocker.patch("bleak.BleakClient", return_value=mock_client)

    sensor = OmronBpSensor(bus, db_session)
    result = await sensor._read_one_notification("AA:BB:CC:DD:EE:FF")

    assert result == payload
    bleak_client_class.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    mock_client.connect.assert_awaited_once()


# Verifies the connect-retry budget: when the first connect()
# attempts fail with BleakError (cuff not yet advertising after the
# user pressed the BT button), the sensor retries up to
# _BP_CONNECT_RETRIES times before giving up. We synthesise two
# failures followed by a success to confirm the loop continues past
# transient errors and that we don't pre-emptively fail-fast.
# Mortality: would fail if the retry loop were dropped (one
# transient flake would surface as a hard error to the GUI).
@pytest.mark.asyncio
async def test_omron_bp_retries_connect_on_transient_failure(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    from bleak.exc import BleakError

    payload = bytes([0x04, 0x78, 0x00, 0x50, 0x00, 0x5D, 0x00, 0x48, 0x00])
    mock_client = _make_mock_connected_client(mocker, payload)
    # First two connects fail, third succeeds.
    mock_client.connect = mocker.AsyncMock(
        side_effect=[
            BleakError("not advertising"),
            BleakError("le-conn-aborted-by-local-host"),
            None,
        ]
    )
    mocker.patch("bleak.BleakClient", return_value=mock_client)
    # Speed up the retry sleep so the test stays fast.
    mocker.patch("asyncio.sleep", new=mocker.AsyncMock())

    sensor = OmronBpSensor(bus, db_session)
    result = await sensor._read_one_notification("AA:BB:CC:DD:EE:FF")

    assert result == payload
    assert mock_client.connect.await_count == 3


# Verifies that exhausting the retry budget surfaces a clear
# RuntimeError naming "pairing mode" so the GUI can prompt the user
# to press the BT button on the cuff again.
# Mortality: would fail if the for/else didn't raise on retry
# exhaustion, or if the message lost the "pairing mode" hint that
# the GUI depends on.
@pytest.mark.asyncio
async def test_omron_bp_raises_when_cuff_not_advertising(
    bus: EventBus, mocker: Any
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    from bleak.exc import BleakError

    mock_client = mocker.MagicMock(name="BleakClient")
    mock_client.connect = mocker.AsyncMock(side_effect=BleakError("nope"))
    mock_client.disconnect = mocker.AsyncMock()
    mocker.patch("bleak.BleakClient", return_value=mock_client)
    mocker.patch("asyncio.sleep", new=mocker.AsyncMock())

    sensor = OmronBpSensor(bus, db_session)
    with pytest.raises(RuntimeError, match="pairing mode"):
        await sensor._read_one_notification("AA:BB:CC:DD:EE:FF")


# Verifies the real path explicitly disconnects after a successful
# measurement. The original code used `async with client_cm:` on the
# established client, which calls __aenter__ (= connect) on an
# already-connected client and raises "Client is already connected".
# The fix uses try/finally with an explicit await client.disconnect().
# Mortality: would fail if the real path went back to async with
# (the disconnect call would never happen) OR if the success path
# skipped disconnect.
@pytest.mark.asyncio
async def test_omron_bp_disconnects_after_measurement_in_real_path(
    bus: EventBus, mocker: Any
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    payload = bytes([0x04, 0x78, 0x00, 0x50, 0x00, 0x5D, 0x00, 0x48, 0x00])
    mock_client = _make_mock_connected_client(mocker, payload)
    mocker.patch("bleak.BleakClient", return_value=mock_client)

    sensor = OmronBpSensor(bus, db_session)
    await sensor._read_one_notification("AA:BB:CC:DD:EE:FF")

    mock_client.disconnect.assert_called_once()


# Verifies _publish_reading emits exactly three MeasurementProposed
# events when a pulse is present, in the canonical order (systolic,
# diastolic, heart_rate). Pins the publication ordering against
# the bug we hit during a manual edit attempt where one of the
# publish calls was accidentally dropped.
# Mortality: would fail if any of the publish calls were dropped.
@pytest.mark.asyncio
async def test_omron_bp_publish_reading_emits_three_events_when_pulse_present(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    sensor = OmronBpSensor(bus, db_session)
    await sensor._publish_reading(
        BpReading(
            systolic_mmhg=128.0,
            diastolic_mmhg=82.0,
            map_mmhg=97.0,
            pulse_bpm=72.0,
        )
    )

    assert len(captured_measurements) == 3
    types = [m.measurement_type for m in captured_measurements]
    assert types == ["systolic_bp", "diastolic_bp", "heart_rate"]
    assert captured_measurements[0].value == pytest.approx(128.0)
    assert captured_measurements[1].value == pytest.approx(82.0)
    assert captured_measurements[2].value == pytest.approx(72.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_connected_client(mocker: Any, payload: bytes) -> Any:
    """Build a mock that mimics a bleak.BleakClient instance.

    Crucially, ``start_notify`` invokes its callback synchronously
    so the ``received`` future inside _read_one_notification resolves
    before ``asyncio.wait_for`` is awaited — otherwise the test would
    block on the 120 s wait.
    """
    client = mocker.MagicMock(name="ConnectedBleakClient")

    async def fake_start_notify(_uuid: str, callback: Any) -> None:
        callback(None, bytearray(payload))

    client.connect = mocker.AsyncMock()
    client.start_notify = mocker.AsyncMock(side_effect=fake_start_notify)
    client.stop_notify = mocker.AsyncMock()
    client.disconnect = mocker.AsyncMock()
    return client
