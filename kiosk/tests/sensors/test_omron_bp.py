"""Omron BP cuff sensor: SIG payload parsing + mock event publishing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


# Verifies the parser correctly extracts the 7-byte SIG Date Time
# field when flags bit 1 is set, AND reads pulse from the offset
# after the timestamp. The bench Pi cuff stamps every measurement
# this way; the parser must surface the timestamp so the kiosk can
# tell a fresh reading from a stored historical one.
# The SIG Date Time field carries no timezone — the parser tags
# the value with the host's local timezone (see _parse_timestamp
# docstring), so we assert the wall-clock fields round-trip
# faithfully and that the result is tz-aware regardless of the
# test machine's timezone.
# Mortality: would fail if the timestamp parser dropped bytes,
# byte-swapped the year, misaligned the post-timestamp pulse
# offset, or stopped attaching a tzinfo.
def test_omron_bp_parser_extracts_optional_timestamp() -> None:
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
    assert reading.taken_at is not None
    assert reading.taken_at.tzinfo is not None
    assert reading.taken_at.replace(tzinfo=None) == datetime(2024, 5, 2, 14, 30, 0)


# Verifies the exact payload the bench Pi's cuff returned on
# 2026-05-06 — proves the parser handles real-world data, not just
# synthetic. Wall-clock fields 2026-05-06 13:25:22, pulse 104,
# systolic 130, diastolic 71. The parser tags the timestamp with
# the host's local tz; the assertion strips tzinfo to round-trip
# the wall-clock fields independently of test-env timezone.
# Mortality: would fail if any decoder drift corrupted the
# bench-validated payload.
def test_omron_bp_parser_decodes_bench_pi_payload() -> None:
    payload = bytes(
        [
            0x1E,  # flags: timestamp + pulse + user-id + status
            0x82,
            0x00,  # systolic 130
            0x47,
            0x00,  # diastolic 71
            0x5A,
            0x00,  # MAP 90
            0xEA,
            0x07,  # year 2026
            0x05,
            0x06,
            0x0D,
            0x19,
            0x16,  # 2026-05-06 13:25:22
            0x68,
            0x00,  # pulse 104
            0x01,  # user 1
            0x00,
            0x00,  # status 0
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.systolic_mmhg == pytest.approx(130.0)
    assert reading.diastolic_mmhg == pytest.approx(71.0)
    assert reading.pulse_bpm == pytest.approx(104.0)
    assert reading.taken_at is not None
    assert reading.taken_at.tzinfo is not None
    assert reading.taken_at.replace(tzinfo=None) == datetime(2026, 5, 6, 13, 25, 22)


# Verifies the timestamp parser returns None when the cuff sends a
# year=0 payload. Some firmwares emit this after a battery pull
# clears the RTC; we'd rather drop the reading than misattribute
# it.
def test_omron_bp_parser_rejects_year_zero_timestamp() -> None:
    payload = bytes(
        [
            0x06,  # flags: time-stamp + pulse-rate present
            0x78,
            0x00,
            0x50,
            0x00,
            0x5D,
            0x00,
            0x00,
            0x00,  # year = 0
            0x05,
            0x02,
            0x0E,
            0x1E,
            0x00,
            0x4B,
            0x00,
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.taken_at is None
    # Pulse should still parse correctly past the bogus timestamp.
    assert reading.pulse_bpm == pytest.approx(75.0)


# Verifies a payload that claims a timestamp but is too short to
# contain one raises rather than silently producing garbage.
def test_omron_bp_parser_rejects_truncated_timestamp() -> None:
    payload = bytes(
        [
            0x02,  # flags: time-stamp present
            0x78,
            0x00,
            0x50,
            0x00,
            0x5D,
            0x00,
            # Only one byte of the 7-byte timestamp — truncated.
            0xE8,
        ]
    )
    with pytest.raises(ValueError, match="timestamp but is truncated"):
        parse_bp_measurement(payload)


# Verifies _is_fresh accepts readings within the freshness window
# and rejects ones outside it. Rejects None timestamps too — without
# a timestamp we can't distinguish a fresh measurement from a stored
# one and prefer to drop the reading.
# Mortality: would fail if the freshness gate accepted unstamped
# readings — the exact regression the 2026-05-06 bench surfaced.
def test_is_fresh_accepts_recent_rejects_old_and_unstamped() -> None:
    from datetime import datetime, timedelta, timezone

    from ginhawa_kiosk.sensors.omron_bp import _BP_FRESHNESS_WINDOW_S, _is_fresh

    now_fixed = datetime(2026, 5, 6, 18, 30, 0, tzinfo=timezone.utc)

    def now() -> datetime:
        return now_fixed

    # Just-taken reading.
    assert _is_fresh(now_fixed, now=now) is True

    # Within the window — 2 minutes ago.
    assert _is_fresh(now_fixed - timedelta(seconds=120), now=now) is True

    # Just outside the window — window + 1 second.
    assert (
        _is_fresh(now_fixed - timedelta(seconds=_BP_FRESHNESS_WINDOW_S + 1), now=now)
        is False
    )

    # The bench's stale reading from 5 hours earlier — definitely stale.
    assert _is_fresh(now_fixed - timedelta(hours=5), now=now) is False

    # No timestamp at all → reject. The kiosk would rather miss a
    # legitimate fresh reading than misattribute a stored old one.
    assert _is_fresh(None, now=now) is False

    # Forward clock skew is also tolerated up to the same window
    # (the cuff's RTC may run a few seconds ahead of the Pi's).
    assert _is_fresh(now_fixed + timedelta(seconds=30), now=now) is True


# Verifies the freshness gate accepts a reading whose timestamp is in
# the future, as long as the skew is within the freshness window.
# Real-world reason: the HEM-7155T tags BLE timestamps "+00:00" but
# encodes its local wall-clock time, so a Pi running UTC in a UTC+N
# deployment sees the cuff's "now" as N hours in the future. The
# 2026-05-06 19:14 bench captured exactly this — 14 readings, all
# future-dated, all dropped pre-fix. Mortality: would fail if the
# gate reverted to a one-sided "must be in the past" check.
def test_is_fresh_future_within_window() -> None:
    from datetime import datetime, timedelta, timezone

    from ginhawa_kiosk.sensors.omron_bp import _is_fresh

    now_fixed = datetime(2026, 5, 6, 18, 30, 0, tzinfo=timezone.utc)

    def now() -> datetime:
        return now_fixed

    # 60 s in the future — well inside the 180 s window.
    assert _is_fresh(now_fixed + timedelta(seconds=60), now=now) is True


# Verifies the symmetric tolerance still has a ceiling: a reading
# stamped 10 minutes in the future is rejected as stale, the same
# way a 10-minute-old reading would be. Pins the upper bound so a
# UTC+8 cuff (8 h ahead) or any other gross skew can't smuggle a
# stale stored reading past the freshness gate.
# Mortality: would fail if the symmetric tolerance grew unbounded
# (e.g., someone replaced the abs check with "always accept future
# readings").
def test_is_fresh_future_beyond_window() -> None:
    from datetime import datetime, timedelta, timezone

    from ginhawa_kiosk.sensors.omron_bp import _is_fresh

    now_fixed = datetime(2026, 5, 6, 18, 30, 0, tzinfo=timezone.utc)

    def now() -> datetime:
        return now_fixed

    # 600 s in the future — well outside the 180 s window.
    assert _is_fresh(now_fixed + timedelta(seconds=600), now=now) is False


# Verifies _parse_timestamp tags the decoded value with the host's
# local tz (not hard-coded UTC). The SIG Date Time field carries no
# timezone metadata and the cuff transmits local wall-clock; the
# parser fix attaches whatever zone /etc/localtime resolves to so
# downstream comparisons against datetime.now(timezone.utc) work.
# Mortality: would fail if the parser reverted to tzinfo=timezone.utc
# (the 2026-05-06 bench bug — every reading appeared 8 h ahead on a
# UTC Pi in a UTC+8 deployment).
def test_parse_timestamp_returns_local_aware() -> None:
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
            0x07,  # year LSB-MSB = 2024
            0x05,
            0x02,
            0x0E,
            0x1E,
            0x00,  # 2024-05-02 14:30:00 (wall-clock)
            0x4B,
            0x00,
        ]
    )
    reading = parse_bp_measurement(payload)
    assert reading.taken_at is not None
    # tzinfo must be attached, regardless of which tz the test runs in.
    assert reading.taken_at.tzinfo is not None
    # Round-tripping through UTC must yield the same instant as
    # interpreting the raw wall-clock as local — that's the fix.
    expected_utc = datetime(2024, 5, 2, 14, 30, 0).astimezone(timezone.utc)
    assert reading.taken_at.astimezone(timezone.utc) == expected_utc


# Regression guard for the 2026-05-06 timezone bug. Simulates a Pi
# running UTC in a non-UTC deployment: the cuff encoded local
# wall-clock time, the parser tagged it with the host's local tz,
# and the freshness gate compares against a fixed UTC "now". A
# reading taken 60 s ago should be fresh — pre-fix it would appear
# (UTC_offset) hours in the future and the gate would drop it.
# Mortality: would fail if the parser reverted to UTC tagging OR
# if _is_fresh stopped doing aware-aware subtraction.
def test_freshness_with_local_cuff_timestamp_pi_in_utc() -> None:
    from ginhawa_kiosk.sensors.omron_bp import _is_fresh

    fixed_utc_now = datetime(2026, 5, 6, 11, 22, 45, tzinfo=timezone.utc)

    def now() -> datetime:
        return fixed_utc_now

    # Construct taken_at the way the parser fix produces it: take a
    # real instant 60 s before "now", express it in the host's local
    # zone so the resulting datetime is aware-local. This mirrors
    # what _parse_timestamp does end-to-end.
    taken_at_local = (fixed_utc_now - timedelta(seconds=60)).astimezone()
    assert taken_at_local.tzinfo is not None  # sanity

    assert _is_fresh(taken_at_local, now=now) is True


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
    r = BpReading(
        systolic_mmhg=120,
        diastolic_mmhg=80,
        map_mmhg=93,
        pulse_bpm=72,
        taken_at=None,
    )
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
        BpReading(
            systolic_mmhg=120,
            diastolic_mmhg=80,
            map_mmhg=93,
            pulse_bpm=72,
            taken_at=None,
        )
    )
    types = [m.measurement_type for m in captured_measurements]
    assert types == ["systolic_bp", "diastolic_bp", "heart_rate"]

    # Without pulse: only two events, no heart_rate.
    captured_measurements.clear()
    await sensor._publish_reading(
        BpReading(
            systolic_mmhg=118,
            diastolic_mmhg=78,
            map_mmhg=91,
            pulse_bpm=None,
            taken_at=None,
        )
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
    payload = _fresh_bp_payload()
    mock_client = _make_mock_connected_client(mocker, payload)
    bleak_client_class = mocker.patch("bleak.BleakClient", return_value=mock_client)

    sensor = OmronBpSensor(bus, db_session)
    reading = await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")

    assert reading is not None
    assert reading.systolic_mmhg == pytest.approx(120.0)
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

    payload = _fresh_bp_payload()
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
    reading = await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")

    assert reading is not None
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
        await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")


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
    payload = _fresh_bp_payload()
    mock_client = _make_mock_connected_client(mocker, payload)
    mocker.patch("bleak.BleakClient", return_value=mock_client)

    sensor = OmronBpSensor(bus, db_session)
    await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")

    mock_client.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# Drain-until-fresh tests (2026-05-06 bench fix)
# ---------------------------------------------------------------------------
#
# The HEM-7155T's store-and-forward BLE model dumps any buffered
# readings on connect. Pre-fix, the kiosk read ONE notification,
# saw it was stale, and disconnected — the freshly-pressed
# measurement that followed never landed. Post-fix, we drain stored
# readings until a fresh one arrives or an outer timeout fires.


# Verifies the drain loop publishes ONLY for the fresh reading when
# the cuff dumps two stale stored readings followed by a fresh one.
# Two stale readings should be logged as ``stored_reading_drained``
# and discarded; the fresh one becomes three MeasurementProposed
# events (systolic / diastolic / heart_rate).
# Mortality: would fail if the drain loop returned early on the
# first notification (the original bug) or if it kept publishing
# stale readings.
@pytest.mark.asyncio
async def test_drains_stored_readings_and_captures_fresh(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    now = datetime.now()  # naive local — matches how the cuff encodes wall-clock
    stale_a = _build_bp_payload(
        now - timedelta(hours=5), systolic=140, diastolic=85, pulse=70
    )
    stale_b = _build_bp_payload(
        now - timedelta(minutes=30), systolic=135, diastolic=82, pulse=68
    )
    fresh = _build_bp_payload(now, systolic=120, diastolic=80, pulse=72)
    mock_client = _make_mock_connected_client(mocker, stale_a, stale_b, fresh)
    mocker.patch("bleak.BleakClient", return_value=mock_client)

    log = _CapturingLogger()
    sensor = OmronBpSensor(bus, db_session)
    sensor._logger = log
    reading = await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")

    assert reading is not None
    assert reading.systolic_mmhg == pytest.approx(120.0)
    assert reading.diastolic_mmhg == pytest.approx(80.0)

    drained = [e for e in log.events if e[1] == "omron_bp.stored_reading_drained"]
    assert len(drained) == 2, f"expected 2 stored_reading_drained logs, got {drained}"
    received = [e for e in log.events if e[1] == "omron_bp.measurement_received"]
    assert len(received) == 1


# Verifies the outer timeout fires when the cuff only dumps stale
# readings and never produces a fresh one. The drain loop should
# log ``omron_bp.fresh_reading_timeout`` with stale_count >= 1 and
# return None — no MeasurementProposed published.
# Mortality: would fail if the timeout were missing (the loop would
# block forever on queue.get) or if stale_count were dropped from
# the log line.
@pytest.mark.asyncio
async def test_timeout_fires_when_no_fresh_reading(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    now = datetime.now()  # naive local — see _build_bp_payload docstring
    stale_a = _build_bp_payload(now - timedelta(hours=5), systolic=140, diastolic=85)
    stale_b = _build_bp_payload(now - timedelta(hours=1), systolic=132, diastolic=84)
    mock_client = _make_mock_connected_client(mocker, stale_a, stale_b)
    mocker.patch("bleak.BleakClient", return_value=mock_client)
    # Compress the wall-clock budget so the test runs quickly.
    mocker.patch("ginhawa_kiosk.sensors.omron_bp._BP_FRESH_READ_TIMEOUT_S", 0.05)

    log = _CapturingLogger()
    sensor = OmronBpSensor(bus, db_session)
    sensor._logger = log
    reading = await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")

    assert reading is None
    assert captured_measurements == []
    timeouts = [e for e in log.events if e[1] == "omron_bp.fresh_reading_timeout"]
    assert len(timeouts) == 1
    level, _event, kwargs = timeouts[0]
    assert level == "warning"
    assert kwargs["stale_count"] >= 1
    assert kwargs["timeout_s"] == pytest.approx(0.05)


# Verifies that when the very first notification is fresh, the
# drain loop returns it immediately with no stored_reading_drained
# logs in between. Confirms the happy-path is unaffected by the
# new draining behaviour.
# Mortality: would fail if the loop spuriously logged
# stored_reading_drained for fresh readings.
@pytest.mark.asyncio
async def test_immediate_fresh_reading(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    fresh = _build_bp_payload(datetime.now(), systolic=118, diastolic=78, pulse=70)
    mock_client = _make_mock_connected_client(mocker, fresh)
    mocker.patch("bleak.BleakClient", return_value=mock_client)

    log = _CapturingLogger()
    sensor = OmronBpSensor(bus, db_session)
    sensor._logger = log
    reading = await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")

    assert reading is not None
    assert reading.systolic_mmhg == pytest.approx(118.0)
    drained = [e for e in log.events if e[1] == "omron_bp.stored_reading_drained"]
    assert drained == []
    received = [e for e in log.events if e[1] == "omron_bp.measurement_received"]
    assert len(received) == 1


# Verifies the drain loop tolerates a malformed indicate (parse
# failure) and continues to the next notification rather than
# aborting the whole drain. The cuff sends a 3-byte garbage payload
# (too short for parse_bp_measurement), then a fresh reading; we
# should publish for the fresh reading and log parse_failed once.
# Mortality: would fail if the parse-failure branch returned
# instead of continuing.
@pytest.mark.asyncio
async def test_parse_failure_skipped_drain_continues(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db_session = mocker.MagicMock(name="DbSession")
    garbage = bytes([0x04, 0x78, 0x00])  # too short
    fresh = _build_bp_payload(datetime.now(), systolic=120, diastolic=80, pulse=72)
    mock_client = _make_mock_connected_client(mocker, garbage, fresh)
    mocker.patch("bleak.BleakClient", return_value=mock_client)

    log = _CapturingLogger()
    sensor = OmronBpSensor(bus, db_session)
    sensor._logger = log
    reading = await sensor._read_notifications_until_fresh("AA:BB:CC:DD:EE:FF")

    assert reading is not None
    parse_failed = [e for e in log.events if e[1] == "omron_bp.parse_failed"]
    assert len(parse_failed) == 1


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
            taken_at=None,
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


def _make_mock_connected_client(mocker: Any, *payloads: bytes) -> Any:
    """Build a mock that mimics a bleak.BleakClient instance.

    ``start_notify`` invokes its callback synchronously for each
    payload in order, so by the time the drain loop awaits
    ``queue.get()``, every payload is already enqueued. This
    keeps tests fast (no real BLE timing) while exercising the
    multi-notification drain path that the 2026-05-06 fix added.
    """
    client = mocker.MagicMock(name="ConnectedBleakClient")

    async def fake_start_notify(_uuid: str, callback: Any) -> None:
        for payload in payloads:
            callback(None, bytearray(payload))

    client.connect = mocker.AsyncMock()
    client.start_notify = mocker.AsyncMock(side_effect=fake_start_notify)
    client.stop_notify = mocker.AsyncMock()
    client.disconnect = mocker.AsyncMock()
    return client


def _build_bp_payload(
    taken_at: datetime,
    *,
    systolic: int = 120,
    diastolic: int = 80,
    pulse: int = 72,
) -> bytes:
    """Construct a SIG Blood Pressure Measurement payload with a timestamp.

    Mirrors the bench-validated layout (flags 0x06 = timestamp +
    pulse-rate present). Integer args go in as the SFLOAT-16
    mantissa with exponent 0, which matches how the cuff actually
    encodes whole-number mmHg / bpm readings.

    Only the wall-clock fields of ``taken_at`` (year/month/day/
    hour/minute/second) end up in the payload; tzinfo is ignored.
    Real cuffs encode local wall-clock with no tz metadata, so
    callers should pass naive local datetimes (``datetime.now()``)
    when simulating fresh readings — the parser now tags decoded
    timestamps with the host's local tz.
    """
    return bytes(
        [
            0x06,
            systolic & 0xFF,
            (systolic >> 8) & 0xFF,
            diastolic & 0xFF,
            (diastolic >> 8) & 0xFF,
            93 & 0xFF,
            (93 >> 8) & 0xFF,
            taken_at.year & 0xFF,
            (taken_at.year >> 8) & 0xFF,
            taken_at.month,
            taken_at.day,
            taken_at.hour,
            taken_at.minute,
            taken_at.second,
            pulse & 0xFF,
            (pulse >> 8) & 0xFF,
        ]
    )


def _fresh_bp_payload(**kwargs: Any) -> bytes:
    """Build a payload stamped 'now' so it passes the freshness gate.

    Uses naive local time (``datetime.now()``) because the parser
    now interprets SIG Date Time bytes as local wall-clock. Using
    UTC fields here would re-introduce the 2026-05-06 bug pattern
    on any non-UTC test machine: the encoded bytes would be UTC
    hour/minute, the parser would interpret them as local, and
    on e.g. a UTC+8 host the round-tripped instant would be 8 h
    in the future of ``datetime.now(timezone.utc)``.
    """
    return _build_bp_payload(datetime.now(), **kwargs)


class _CapturingLogger:
    """Records structlog-style log calls into a list for assertion.

    The OmronBpSensor instantiates its own logger via
    ``structlog.get_logger``; tests overwrite ``sensor._logger``
    with this in-memory recorder so they can assert on event
    names and structured fields without coupling to structlog's
    output renderer.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **kwargs: Any) -> None:
        self.events.append((level, event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._record("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._record("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._record("error", event, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._record("debug", event, **kwargs)
