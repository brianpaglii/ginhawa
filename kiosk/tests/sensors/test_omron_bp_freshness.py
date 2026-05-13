"""Session-floor freshness for the Omron BP cuff (ADR-0020).

The cuff is store-and-forward: every reconnect re-delivers the last
measurement. The absolute 180 s freshness window alone cannot
distinguish "stored from the previous session, ended 30 s ago" from
"taken just now for this session." :func:`_is_fresh` now takes an
optional ``session_floor`` keyword — the kiosk stamps it at the
moment it emits :class:`BpMeasurementRequested` and the BP handler
passes it through so readings predating the floor (minus a small
skew) are rejected even when they fall inside the absolute window.

These tests pin the two-gate semantics and the handler-state hygiene
(floor cleared on exit). They are siblings of the existing
``test_omron_bp.py`` freshness tests, which pin the absolute-window
behaviour without a floor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from ginhawa_kiosk.fsm import EventBus
from ginhawa_kiosk.fsm.event_bus import BpMeasurementRequested
from ginhawa_kiosk.sensors.omron_bp import (
    OmronBpSensor,
    _is_fresh,
)


# Verifies the typical happy path: the cuff captures a reading
# shortly after the kiosk stamped the session floor, and the kiosk
# evaluates freshness moments later. Both gates pass, the reading
# is treated as fresh.
# Mortality: would fail if the floor check rejected a reading whose
# taken_at is strictly after the floor.
def test_fresh_reading_after_floor_accepted() -> None:
    t = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    taken_at = t + timedelta(seconds=10)
    now_fixed = t + timedelta(seconds=30)
    assert _is_fresh(taken_at, now=lambda: now_fixed, session_floor=t)


# Verifies the bug-class fix: a stored reading from a session that
# ended seconds ago is still inside the 180 s absolute window but
# was taken BEFORE the new session's floor — the kiosk must reject
# it. This is the exact failure mode docs/audits/
# 2026-05-13-bp-stale-readings-audit.md identifies.
# Mortality: would fail if the floor check were missing or applied
# only inside the absolute window without the predates-floor test.
def test_reading_before_floor_minus_skew_rejected() -> None:
    t = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    # Reading taken 30 s before the floor — well outside the 10 s
    # skew, well inside the 180 s absolute window.
    taken_at = t - timedelta(seconds=30)
    now_fixed = t + timedelta(seconds=5)
    assert _is_fresh(taken_at, now=lambda: now_fixed, session_floor=t) is False


# Verifies the skew tolerance: a reading taken a few seconds BEFORE
# the floor (cuff RTC drift, or citizen pressing START just before
# the kiosk's MEASURING_VITALS entry stamp) is still accepted. The
# floor is "minus skew_s," not strict.
# Mortality: would fail if the floor were applied as a hard >=
# without any tolerance.
def test_reading_within_skew_of_floor_accepted() -> None:
    t = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    # 5 s before the floor — inside the 10 s skew window.
    taken_at = t - timedelta(seconds=5)
    now_fixed = t + timedelta(seconds=5)
    assert _is_fresh(taken_at, now=lambda: now_fixed, session_floor=t)


# Verifies the absolute window is still the outer guard. A reading
# from hours ago is rejected even if the floor would also reject it
# — the kiosk does not rely on the floor alone, because an operator-
# misconfigured cuff RTC could in principle place "before the floor"
# in the deep past, which the floor check would catch but at the
# cost of legibility in logs. The two gates are independent.
# Mortality: would fail if the absolute-window check were
# accidentally collapsed into the floor check.
def test_reading_outside_absolute_window_rejected_regardless_of_floor() -> None:
    t = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    # 400 s before the floor — outside both gates.
    taken_at = t - timedelta(seconds=400)
    now_fixed = t + timedelta(seconds=5)
    assert _is_fresh(taken_at, now=lambda: now_fixed, session_floor=t) is False


# Verifies the legacy single-gate behaviour is preserved when no
# floor is supplied. Existing call sites — tests that exercise
# ``_is_fresh(taken_at, now=...)`` without a request lifecycle, and
# the helper's pre-ADR-0020 contract — continue to compare against
# the absolute window alone.
# Mortality: would fail if the floor were treated as required (e.g.,
# defaulted to ``datetime.now``) and rejected readings whose
# taken_at predates that implicit floor.
def test_session_floor_none_falls_back_to_absolute_window() -> None:
    now_fixed = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    # 30 s before now, well within the absolute window.
    taken_at = now_fixed - timedelta(seconds=30)
    assert _is_fresh(taken_at, now=lambda: now_fixed) is True
    assert _is_fresh(taken_at, now=lambda: now_fixed, session_floor=None) is True


# Verifies the handler clears _session_floor on exit so a stale
# floor from a completed request cannot bleed into the next session.
# Drives the bus subscription end-to-end: a request that completes
# (drain-timeout, no reading) must leave _session_floor == None.
# Mortality: would fail if the finally were dropped — and a
# subsequent direct call to _read_notifications_until_fresh would
# unexpectedly inherit the prior floor.
@pytest.mark.asyncio
async def test_session_floor_cleared_after_handler_exit(mocker: Any) -> None:
    bus = EventBus()
    db_session = mocker.MagicMock(name="DbSession")
    sensor = OmronBpSensor(bus, db_session)
    sensor._mac = "AA:BB:CC:DD:EE:FF"
    sensor._running = True
    bus.subscribe(BpMeasurementRequested, sensor._handle_request)

    # Stub _read_notifications_until_fresh to a quick no-reading
    # return so we don't touch BLE. The handler still goes through
    # its full set/clear floor lifecycle.
    captured_floor: list[datetime | None] = []

    async def fake_read(_mac: str) -> None:
        captured_floor.append(sensor._session_floor)
        return None

    mocker.patch.object(sensor, "_read_notifications_until_fresh", fake_read)

    floor_iso = "2026-05-13T12:00:00+00:00"
    await bus.publish(BpMeasurementRequested(session_floor=floor_iso))

    # Inside the handler the floor was set to the parsed value.
    assert captured_floor == [datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)]
    # On handler exit the floor is cleared.
    assert sensor._session_floor is None


# Regression guard for ADR-0020's headline scenario: two back-to-
# back vitals sessions, < 180 s apart. Direct unit-level proof that
# session 1's reading would be accepted under the legacy gate (no
# floor) but is correctly rejected under the new two-gate predicate
# (with floor stamped at session 2's start).
# Mortality: would fail if the floor weren't tight enough to catch
# a reading from "a minute ago" — i.e., the exact bug.
def test_back_to_back_sessions_reject_prior_reading() -> None:
    session1_request = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    # Citizen 1 took their BP 30 s after the kiosk emitted session 1's
    # request — well within the skew margin and the absolute window.
    citizen1_reading = session1_request + timedelta(seconds=30)
    # Session 2 starts 90 s after citizen 1's reading was captured.
    # The cuff still holds citizen 1's reading.
    session2_request = citizen1_reading + timedelta(seconds=90)
    now_at_session2 = session2_request + timedelta(seconds=5)

    # Legacy gate (no floor) accepts citizen 1's stored reading —
    # this IS the bug.
    assert _is_fresh(citizen1_reading, now=lambda: now_at_session2) is True
    # New gate with session 2's floor stamped correctly rejects it.
    assert (
        _is_fresh(
            citizen1_reading,
            now=lambda: now_at_session2,
            session_floor=session2_request,
        )
        is False
    )
