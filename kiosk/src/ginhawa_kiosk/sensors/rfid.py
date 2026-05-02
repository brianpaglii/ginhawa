"""RFID reader: mock + MFRC522 hardware implementation.

The RC522 is a polled reader on the Pi's SPI bus (SDA→GPIO8/CE0,
SCK→GPIO11, MOSI→GPIO10, MISO→GPIO9, IRQ→GPIO24, RST→GPIO25, plus
3.3V and GND). It reads 13.56 MHz MIFARE Classic and NTAG cards.
The driver continuously asks "is a card present?"; when a card is
detected, the loop reads the UID and publishes an :class:`RfidScanned`
event to the bus.

CRITICAL — lazy imports of Pi-only deps
---------------------------------------
``RPi.GPIO``, ``spidev``, and ``mfrc522`` are imported INSIDE
:meth:`Mfrc522RfidReader.__init__`, NOT at module top level. None
of those packages have x86_64 wheels (RPi.GPIO needs Pi kernel
headers); importing them at top level would break ``import
ginhawa_kiosk.sensors.rfid`` on a development laptop. The mock
implementation must remain importable without those deps because
that is the entire point of MOCK_HARDWARE.

The factory in :mod:`ginhawa_kiosk.sensors.__init__` picks mock or
real based on ``settings.MOCK_HARDWARE``. The lazy-import
invariant is pinned by
:func:`tests.sensors.test_rfid.test_rfid_module_does_not_import_pi_specific_dependencies_at_top_level`
— if that test fails, a top-level import has crept in.

Debouncing
----------
The same physical card stays in the field while a citizen is at the
kiosk. Without debounce, the polling loop would emit hundreds of
RfidScanned events per session. We track the last-seen monotonic
timestamp PER UID and ignore reads of the same UID within a 2-second
window. A different UID scanned in quick succession is NOT
suppressed — the dict is keyed on UID, not on a single global last
time, so a citizen handing the kiosk to a colleague (different card)
is recognised immediately.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any

import structlog

from ..fsm.event_bus import EventBus, RfidScanned
from .base import Sensor


_DEBOUNCE_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.1


# ---------------------------------------------------------------------------
# Mock — for development on laptop / CI / integration tests
# ---------------------------------------------------------------------------


class MockRfidReader(Sensor):
    """In-memory RFID reader. Tests / dev call :meth:`simulate_tap`."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def simulate_tap(self, uid: str) -> None:
        """Publish an :class:`RfidScanned` event with the given UID.

        UIDs are normalised to uppercase hex with no separators —
        different physical readers emit different cases and the
        database needs a canonical form.
        """
        normalised = uid.upper()
        await self._bus.publish(RfidScanned(uid=normalised))


# ---------------------------------------------------------------------------
# Real — MFRC522 over SPI on the Pi
# ---------------------------------------------------------------------------


class Mfrc522RfidReader(Sensor):
    """Polled MFRC522 RFID reader on the Pi's SPI bus.

    Construction triggers the lazy import of ``mfrc522`` /
    ``RPi.GPIO``. Tests bypass the import by passing in their own
    ``reader`` and ``gpio_module`` keyword arguments.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        reader: Any | None = None,
        gpio_module: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._bus = bus
        self._clock = clock or time.monotonic
        self._logger = structlog.get_logger("rfid.mfrc522")

        if reader is None:  # pragma: no cover - Pi-only construction path
            # Lazy imports — Pi-only. See module docstring.
            from mfrc522 import SimpleMFRC522

            reader = SimpleMFRC522()
        if gpio_module is None:  # pragma: no cover - Pi-only construction
            import RPi.GPIO as GPIO  # noqa: N814 - vendor module name

            gpio_module = GPIO

        self._reader = reader
        self._gpio = gpio_module

        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # uid (uppercase hex) → last-published monotonic timestamp.
        self._last_seen: dict[str, float] = {}

    async def start(self) -> None:
        if self._running:  # pragma: no cover - idempotency guard
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="mfrc522-poll"
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._gpio is not None:
            try:
                self._gpio.cleanup()
            except Exception as exc:  # pragma: no cover - hardware path
                self._logger.warning("rfid.gpio_cleanup_failed", error=str(exc))
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Internals — testable in isolation
    # ------------------------------------------------------------------

    def _maybe_uid_to_publish(self, uid_int: int | None) -> str | None:
        """Pure logic: returns the hex UID to publish, or None.

        Returns None when:
          * ``uid_int`` is None (no card present), or
          * the UID was last published within the debounce window.
        """
        if uid_int is None:
            return None
        uid_hex = f"{uid_int:08X}"
        now = self._clock()
        last = self._last_seen.get(uid_hex)
        if last is not None and (now - last) < _DEBOUNCE_SECONDS:
            return None
        self._last_seen[uid_hex] = now
        return uid_hex

    async def _process_one_read(self, uid_int: int | None) -> None:
        """Async-side of a single read: publish to the bus iff
        the debounce gate lets it through. Tests await this directly."""
        uid_hex = self._maybe_uid_to_publish(uid_int)
        if uid_hex is not None:
            await self._bus.publish(RfidScanned(uid=uid_hex))

    def _poll_loop(self) -> None:  # pragma: no cover - thread + hardware
        # Runs in the background thread. The asyncio publish is
        # marshalled back to the main loop via run_coroutine_threadsafe.
        while not self._stop_event.is_set():
            try:
                uid_int = self._reader.read_id_no_block()
            except Exception as exc:
                self._logger.warning("rfid.read_failed", error=str(exc))
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue
            uid_hex = self._maybe_uid_to_publish(uid_int)
            if uid_hex is not None and self._loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._bus.publish(RfidScanned(uid=uid_hex)),
                    self._loop,
                )
            time.sleep(_POLL_INTERVAL_SECONDS)
