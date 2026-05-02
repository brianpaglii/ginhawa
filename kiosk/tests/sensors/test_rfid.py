"""RFID reader: mock + Mfrc522 hardware-bypassed."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session  # noqa: F401  (used by fixture types)

from ginhawa_kiosk.fsm import EventBus, RfidScanned
from ginhawa_kiosk.sensors.rfid import Mfrc522RfidReader, MockRfidReader


# Verifies the mock publishes one RfidScanned event per simulate_tap.
# Mortality: would fail if MockRfidReader did not call bus.publish.
@pytest.mark.asyncio
async def test_mock_rfid_reader_publishes_event_on_simulate_tap(
    bus: EventBus, captured_rfid: list[RfidScanned]
) -> None:
    reader = MockRfidReader(bus)
    await reader.simulate_tap("A3F2C901")
    assert len(captured_rfid) == 1
    assert captured_rfid[0].uid == "A3F2C901"


# Verifies UIDs are normalised to uppercase before publication. The
# database needs a canonical form so two readers with different
# emit-cases don't double-register the same physical card.
# Mortality: would fail if UID normalisation were dropped.
@pytest.mark.asyncio
async def test_mock_rfid_reader_normalizes_uid_to_uppercase(
    bus: EventBus, captured_rfid: list[RfidScanned]
) -> None:
    reader = MockRfidReader(bus)
    await reader.simulate_tap("a3f2c901")
    assert len(captured_rfid) == 1
    assert captured_rfid[0].uid == "A3F2C901"


# Verifies the lazy-import invariant: importing
# ginhawa_kiosk.sensors.rfid on a non-Pi machine MUST NOT pull in
# RPi.GPIO, spidev, or mfrc522 at module top level. Those packages
# have no x86_64 wheels — top-level import would break the dev
# laptop pathway.
# Mortality: would fail if any Pi-only module were imported at module
# top level instead of inside Mfrc522RfidReader.__init__.
def test_rfid_module_does_not_import_pi_specific_dependencies_at_top_level() -> None:
    # Drop any cached imports the rest of the suite may have left behind
    # so this test is independent of test order.
    for name in list(sys.modules):
        if name.startswith("RPi") or name in {"spidev", "mfrc522"}:
            sys.modules.pop(name, None)
    sys.modules.pop("ginhawa_kiosk.sensors.rfid", None)

    import ginhawa_kiosk.sensors.rfid  # noqa: F401

    assert "RPi" not in sys.modules and "RPi.GPIO" not in sys.modules
    assert "spidev" not in sys.modules
    assert "mfrc522" not in sys.modules


# Verifies debounce: the same UID seen 5 times within 1 second
# produces exactly one event. Without debounce a card held in the
# field while the citizen is at the kiosk would emit hundreds of
# events per session.
# Mortality: would fail if debounce logic were removed or threshold
# changed.
@pytest.mark.asyncio
async def test_mfrc522_reader_debounces_repeat_reads_of_same_uid(
    bus: EventBus, captured_rfid: list[RfidScanned]
) -> None:
    fake_clock = _FakeClock()
    reader = Mfrc522RfidReader(
        bus,
        reader=MagicMock(),
        gpio_module=MagicMock(),
        clock=fake_clock.now,
    )
    uid_int = 0xA3F2C901
    for _ in range(5):
        await reader._process_one_read(uid_int)
        fake_clock.advance(0.1)  # 5 reads spread over 0.5 s, all <2 s

    assert len(captured_rfid) == 1
    assert captured_rfid[0].uid == "A3F2C901"


# Verifies debounce is keyed on (uid, time), NOT on a single global
# last-time. If a citizen hands the kiosk to a colleague (different
# card), the new card must register immediately even if it falls
# inside the old card's debounce window.
# Mortality: would fail if debounce were keyed on time only.
@pytest.mark.asyncio
async def test_mfrc522_reader_does_not_debounce_distinct_uids(
    bus: EventBus, captured_rfid: list[RfidScanned]
) -> None:
    fake_clock = _FakeClock()
    reader = Mfrc522RfidReader(
        bus,
        reader=MagicMock(),
        gpio_module=MagicMock(),
        clock=fake_clock.now,
    )
    await reader._process_one_read(0xA3F2C901)
    fake_clock.advance(0.05)  # well inside 2 s
    await reader._process_one_read(0xB7E45620)

    assert len(captured_rfid) == 2
    assert captured_rfid[0].uid == "A3F2C901"
    assert captured_rfid[1].uid == "B7E45620"


# Verifies the debounce timestamp is refreshed on each successful
# emission so a card that is taken away and re-tapped after the
# 2-second window emits a second event.
# Mortality: would fail if the debounce timestamp were never
# refreshed.
@pytest.mark.asyncio
async def test_mfrc522_reader_emits_uid_after_debounce_window(
    bus: EventBus, captured_rfid: list[RfidScanned]
) -> None:
    fake_clock = _FakeClock()
    reader = Mfrc522RfidReader(
        bus,
        reader=MagicMock(),
        gpio_module=MagicMock(),
        clock=fake_clock.now,
    )
    uid_int = 0xA3F2C901
    await reader._process_one_read(uid_int)  # t=0 → emit
    fake_clock.advance(3.0)  # past the 2 s window
    await reader._process_one_read(uid_int)  # t=3 → emit again

    assert len(captured_rfid) == 2


# Verifies the mock's start/stop/is_running shape so the lifecycle
# manager can call all three uniformly across mock and real.
@pytest.mark.asyncio
async def test_mock_rfid_reader_lifecycle(bus: EventBus) -> None:
    reader = MockRfidReader(bus)
    assert reader.is_running is False
    await reader.start()
    assert reader.is_running is True
    await reader.stop()
    assert reader.is_running is False


# Verifies start/stop wiring against a fake reader. Start spawns a
# polling thread; stop signals it and joins. We assert is_running
# flips correctly and gpio.cleanup() is called on shutdown.
@pytest.mark.asyncio
async def test_mfrc522_reader_start_stop_lifecycle(bus: EventBus) -> None:
    fake_reader = MagicMock()
    # read_id_no_block returns None immediately — no card present.
    fake_reader.read_id_no_block.return_value = None
    fake_gpio = MagicMock()
    reader = Mfrc522RfidReader(bus, reader=fake_reader, gpio_module=fake_gpio)

    assert reader.is_running is False
    await reader.start()
    assert reader.is_running is True
    await reader.stop()
    assert reader.is_running is False
    fake_gpio.cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """Deterministic monotonic-style clock for debounce tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds
