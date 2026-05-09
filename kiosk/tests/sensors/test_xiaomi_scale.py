"""Xiaomi Smart Scale S200 sensor: mock + real (BLE-bypassed)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from ginhawa_kiosk.fsm import EventBus, MeasurementProposed, SessionResetForSensors
from ginhawa_kiosk.sensors.base import SensorUnavailable
from ginhawa_kiosk.sensors.xiaomi_scale import (
    _GATE_WARMUP_SECONDS,
    _WeightStabilityGate,
    MockXiaomiScale,
    XiaomiScaleSensor,
    extract_mass_kg,
)

from .conftest import set_device_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MassEntity:
    """Mimic the xiaomi_ble entity object exposed under .native_value."""

    def __init__(self, native_value: float) -> None:
        self.native_value = native_value


def _mass_advertisement(kg: float) -> dict[str, Any]:
    """A synthetic entity_values payload carrying mass."""
    return {"mass": _MassEntity(kg)}


def _signal_strength_only_advertisement() -> dict[str, Any]:
    """A synthetic entity_values payload with only signal_strength —
    Xiaomi advertises these between measurements."""
    return {"signal_strength": _MassEntity(-72.0)}


async def _feed_kg(sensor: XiaomiScaleSensor, *values: float) -> None:
    for v in values:
        await sensor._on_sensor_update(_mass_advertisement(v))


def _skip_gate_warmup(sensor: XiaomiScaleSensor) -> None:
    """Backdate the gate's unlock timestamp so the post-unlock warmup
    window has already elapsed. Tests that exercise the
    publish → reset → re-publish cycle care about gate semantics, not
    real-time scheduling; the warmup is a production race fix that
    these tests would otherwise have to wait 8 seconds to clear.
    Safe to call when the gate has never been unlocked (the field is
    None) — the early-return inside accept() already covers that
    case."""
    if sensor._gate._unlocked_at is not None:
        sensor._gate._unlocked_at -= 1_000.0


# ---------------------------------------------------------------------------
# Lifecycle + adapter helpers
# ---------------------------------------------------------------------------


# Verifies the mock's lifecycle: start/stop flip is_running. The
# stability gate behaviour is exercised separately below; this test
# is intentionally narrow.
@pytest.mark.asyncio
async def test_mock_xiaomi_scale_lifecycle(bus: EventBus) -> None:
    scale = MockXiaomiScale(bus)
    assert scale.is_running is False
    await scale.start()
    assert scale.is_running is True
    await scale.stop()
    assert scale.is_running is False


# Verifies extract_mass_kg returns None when no mass key is present
# (covers the helper directly, since the sensor pipeline above only
# exercises one path through it).
def test_extract_mass_kg_returns_none_when_no_mass_key() -> None:
    assert extract_mass_kg({"signal_strength": _MassEntity(-72.0)}) is None
    assert extract_mass_kg({}) is None


# Verifies signal-strength-only advertisements (no mass entity) are
# silently ignored. Xiaomi emits these between actual measurements;
# the sensor must not treat them as readings.
# Mortality: would fail if the mass-presence check were removed.
@pytest.mark.asyncio
async def test_xiaomi_scale_ignores_signal_strength_only_advertisements(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db = mocker.MagicMock(name="DbSession")
    sensor = XiaomiScaleSensor(bus, db)

    await sensor._on_sensor_update(_signal_strength_only_advertisement())
    assert captured_measurements == []


# Verifies start() refuses to run if no bindkey is in device_config.
# A kiosk cannot be commissioned without one (per ADR-0017); failing
# loud beats silently scanning with no decryption.
# Mortality: would fail if the kiosk silently accepted a missing
# bindkey.
@pytest.mark.asyncio
async def test_xiaomi_scale_raises_on_missing_bindkey(
    bus: EventBus, db_session: Session
) -> None:
    sensor = XiaomiScaleSensor(bus, db_session)
    with pytest.raises(SensorUnavailable, match="bindkey missing"):
        await sensor.start()


# Verifies start() also refuses on a malformed (non-hex) bindkey,
# rather than letting bytes.fromhex blow up later mid-scan.
@pytest.mark.asyncio
async def test_xiaomi_scale_raises_on_malformed_bindkey(
    bus: EventBus, db_session: Session
) -> None:
    set_device_config(db_session, "xiaomi_scale_bindkey", "not-hex-at-all")
    sensor = XiaomiScaleSensor(bus, db_session)
    with pytest.raises(SensorUnavailable, match="not valid hex"):
        await sensor.start()


# ---------------------------------------------------------------------------
# Stability gate + session lock
# ---------------------------------------------------------------------------
#
# The S200 broadcasts mass roughly every 5 s while a person stands
# on the scale. Pre-fix, the kiosk emitted one MeasurementProposed
# per advertisement (7 events in 30 s on the 2026-05-06 bench),
# producing flickering displays and multiple measurement rows per
# session. The new gate captures one stable median per session and
# locks until SessionResetForSensors releases it.


# Verifies the gate publishes exactly once after K stable readings,
# with the value being the median of the buffer. K=3, tolerance
# 0.2 kg by default.
# Mortality: would fail if the gate published before K readings, or
# if it published the last value instead of the median.
@pytest.mark.asyncio
async def test_weight_stability_publishes_after_k_readings(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db = mocker.MagicMock(name="DbSession")
    sensor = XiaomiScaleSensor(bus, db)

    await _feed_kg(sensor, 70.1, 70.2, 70.1)

    assert len(captured_measurements) == 1
    assert captured_measurements[0].measurement_type == "weight"
    assert captured_measurements[0].value == pytest.approx(70.1)
    assert captured_measurements[0].unit == "kg"


# Verifies a buffer with a spread larger than the tolerance does NOT
# publish. The user is presumably still settling on the scale; the
# kiosk should keep waiting.
# Mortality: would fail if the spread check were missing or
# inverted.
@pytest.mark.asyncio
async def test_weight_stability_rejects_unstable_buffer(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db = mocker.MagicMock(name="DbSession")
    sensor = XiaomiScaleSensor(bus, db)

    # Spread = 10 kg; well outside the 0.2 kg tolerance.
    await _feed_kg(sensor, 65.0, 75.0, 70.0)

    assert captured_measurements == []


# Verifies that once the gate publishes, further stable readings
# are suppressed for the rest of the session — the citizen got their
# captured weight and the scale shouldn't keep spamming events as
# they stand on it.
# Mortality: would fail if the lock weren't engaged on publish, or
# if a second publish slipped through.
@pytest.mark.asyncio
async def test_session_lock_suppresses_after_publish(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db = mocker.MagicMock(name="DbSession")
    sensor = XiaomiScaleSensor(bus, db)

    # First K stable readings → publish.
    await _feed_kg(sensor, 70.0, 70.0, 70.0)
    assert len(captured_measurements) == 1

    # Three more stable readings at the same value — locked, so
    # nothing should publish.
    await _feed_kg(sensor, 70.0, 70.0, 70.0)
    assert len(captured_measurements) == 1


# Verifies that publishing SessionResetForSensors releases the lock
# and lets a fresh session capture a new weight. This is the path
# main_window takes on every transition into IDLE / LANGUAGE_SELECT.
# Mortality: would fail if the bus subscription weren't installed
# in start(), or if reset_for_new_session() didn't clear the buffer.
@pytest.mark.asyncio
async def test_session_lock_releases_on_reset(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db = mocker.MagicMock(name="DbSession")
    sensor = XiaomiScaleSensor(bus, db)

    # First session: publish.
    await _feed_kg(sensor, 70.0, 70.0, 70.0)
    assert len(captured_measurements) == 1

    # Reset (direct method call — exercises the public surface that
    # the bus path delegates to).
    sensor.reset_for_new_session()
    _skip_gate_warmup(sensor)

    # Second session: another publish.
    await _feed_kg(sensor, 65.0, 65.0, 65.0)
    assert len(captured_measurements) == 2
    assert captured_measurements[1].value == pytest.approx(65.0)


# Verifies the rolling-buffer behaviour holds publication off until
# the user actually stabilises. Simulates a citizen who steps on,
# shifts, steps half-off, then settles — only the trailing K stable
# readings should trigger a publish.
# Mortality: would fail if the gate published mid-shift, or if the
# deque wasn't actually rolling (max-min over the FULL history
# would never settle).
@pytest.mark.asyncio
async def test_stability_window_handles_user_shifting(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db = mocker.MagicMock(name="DbSession")
    sensor = XiaomiScaleSensor(bus, db)

    # 70 → buffer=[70], len<K, no publish
    # 65 → buffer=[70,65], len<K, no publish
    # 70 → buffer=[70,65,70], spread=5 > 0.2, no publish
    # 70 → buffer=[65,70,70], spread=5 > 0.2, no publish
    # 70.1 → buffer=[70,70,70.1], spread=0.1 ≤ 0.2, PUBLISH
    await _feed_kg(sensor, 70.0, 65.0, 70.0, 70.0, 70.1)

    assert len(captured_measurements) == 1
    assert captured_measurements[0].value == pytest.approx(70.0)


# Verifies the bus-driven reset path: publishing SessionResetForSensors
# on the bus reaches the sensor's handler and unlocks the gate.
# This is the production path (main_window publishes the event on
# state changes into IDLE / LANGUAGE_SELECT); the direct
# reset_for_new_session() call covers the API surface for callers
# that hold a sensor ref.
# We wire the subscription directly rather than going through
# start(), since start() is hardware-bound (BleakScanner, xiaomi_ble
# decode setup); the wiring contract is what matters here.
# Mortality: would fail if the handler didn't call into the gate
# (which is what main_window depends on for cross-session cleanup).
@pytest.mark.asyncio
async def test_session_reset_via_bus_event_unlocks_gate(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    mocker: Any,
) -> None:
    db = mocker.MagicMock(name="DbSession")
    sensor = XiaomiScaleSensor(bus, db)
    bus.subscribe(SessionResetForSensors, sensor._on_session_reset)

    await _feed_kg(sensor, 70.0, 70.0, 70.0)
    assert len(captured_measurements) == 1
    assert sensor._gate.is_locked() is True

    await bus.publish(SessionResetForSensors())
    assert sensor._gate.is_locked() is False
    _skip_gate_warmup(sensor)

    await _feed_kg(sensor, 60.0, 60.0, 60.0)
    assert len(captured_measurements) == 2


# Verifies the mock's simulate_weight goes through the same gate, so
# any code that talks to the scale in MOCK_HARDWARE mode behaves the
# same way as real hardware: no spurious flicker, one capture per
# session, releases on reset_for_new_session().
@pytest.mark.asyncio
async def test_mock_xiaomi_scale_uses_stability_gate(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    scale = MockXiaomiScale(bus)
    await scale.simulate_weight(68.0)
    await scale.simulate_weight(68.0)
    assert captured_measurements == []  # buffer not full yet

    await scale.simulate_weight(68.0)
    assert len(captured_measurements) == 1
    assert captured_measurements[0].value == pytest.approx(68.0)

    await scale.simulate_weight(68.0)  # locked
    assert len(captured_measurements) == 1

    scale.reset_for_new_session()
    # Same warmup-skip dance as the real-sensor tests above — see the
    # _skip_gate_warmup docstring for why these tests don't wait
    # 8 seconds of wall-clock time.
    if scale._gate._unlocked_at is not None:
        scale._gate._unlocked_at -= 1_000.0
    await scale.simulate_weight(72.0)
    await scale.simulate_weight(72.0)
    await scale.simulate_weight(72.0)
    assert len(captured_measurements) == 2
    assert captured_measurements[1].value == pytest.approx(72.0)


# ---------------------------------------------------------------------------
# Post-unlock warmup window
# ---------------------------------------------------------------------------
#
# Bench (2026-05-09) showed weight publishes within 50 ms – 1 s of
# the gate_unlocked event — far faster than 3 stable readings × 5 s
# broadcast cadence allows. Either the BleAdapterLock pause/resume
# cycle re-delivers cached advertisements when the scanner resumes,
# or xiaomi-ble caches state internally and emits on next event
# regardless of buffer history. The warmup window in
# _WeightStabilityGate.accept() drops readings for ``warmup_seconds``
# after each unlock so the buffer can only fill with genuinely-fresh
# readings from the citizen who just stepped on.


# Verifies the warmup drops readings that arrive immediately after
# unlock. Mortality: would fail if the warmup check were removed,
# placed AFTER the buffer append (so the buffer would still capture
# stale readings), or if the constant were lowered to a value
# smaller than typical broadcast jitter.
def test_gate_drops_readings_during_warmup_window() -> None:
    gate = _WeightStabilityGate()
    gate.unlock()
    # Three back-to-back accepts, each well within the 8 s warmup,
    # all return None and DON'T fill the buffer.
    assert gate.accept(70.0) is None
    assert gate.accept(70.0) is None
    assert gate.accept(70.0) is None
    # Buffer is still empty — the warmup short-circuited before
    # append. (The readings would have triggered a publish in the
    # absence of warmup, since they're K stable values.)
    assert len(gate._buffer) == 0


# Verifies once the warmup elapses the gate behaves normally:
# K stable readings publish the median. Uses a constructor-injected
# warmup_seconds=0.0 to make the test deterministic without
# real-time waits or time.monotonic patching.
def test_gate_accepts_after_warmup_elapsed() -> None:
    gate = _WeightStabilityGate(warmup_seconds=0.0)
    gate.unlock()
    # warmup is 0 — first reading already passes the gate.
    assert gate.accept(70.0) is None  # buffer fills, len<K
    assert gate.accept(70.0) is None  # buffer fills, len<K
    published = gate.accept(70.0)  # buffer full + stable → publish
    assert published == pytest.approx(70.0)


# Verifies the warmup does NOT apply on a fresh gate (no prior
# unlock). _unlocked_at is None on a freshly-constructed gate, and
# the early-return path inside accept() must skip the warmup check
# entirely — otherwise every kiosk boot would lose its first
# weight to the warmup window. The default-warmup constant is used
# here intentionally: this is the production code path.
def test_gate_warmup_does_not_apply_before_first_unlock() -> None:
    gate = _WeightStabilityGate()  # default _GATE_WARMUP_SECONDS
    assert gate._unlocked_at is None
    # Three stable readings → publish immediately, even though no
    # 8-second wait has elapsed since gate construction.
    assert gate.accept(70.0) is None
    assert gate.accept(70.0) is None
    published = gate.accept(70.0)
    assert published == pytest.approx(70.0)


# Sanity check that the prod constant is in the documented ballpark
# (one full broadcast cycle plus margin). Mortality: would fail if
# someone tuned the constant to a sub-cycle value, which would
# defeat the warmup's purpose (one cached broadcast at ~5 s after
# unlock could still satisfy the gate).
def test_gate_warmup_constant_covers_full_broadcast_cycle() -> None:
    # Xiaomi S200 broadcasts every ~5 s. Warmup must cover at least
    # one full cycle so a single cached re-emission can't slip past.
    assert _GATE_WARMUP_SECONDS >= 5.0
