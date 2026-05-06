"""Coordinator for serialised BLE adapter access.

CLAUDE.md absolute rule: "Never run more than one BLE operation
concurrently. BlueZ on the Pi is not concurrent-safe for our use.
The session FSM is the SOLE serialiser of BLE access — it holds a
lock and owns each BLE device's lifecycle."

The 2026-05-06 bench surfaced exactly this failure mode: the
Xiaomi scale's ``BleakScanner`` runs continuously from boot to
catch opportunistic advertisements, and the Omron BP cuff's
``BleakClient.connect()`` collides with the active scan and gets
``[org.bluez.Error.InProgress]`` on every retry. With the kiosk
stopped (and the Xiaomi scanner with it), the same Omron sensor
connects in ~100 ms and reads the BP cleanly.

Contract
--------

* Sensors that hold the adapter via passive scan
  (:class:`XiaomiScaleSensor`) register a ``pause`` and ``resume``
  callback at construction. Pause stops the active
  ``BleakScanner``; resume restarts it.
* Sensors that need exclusive access for a directed connect
  (:class:`OmronBpSensor`) wrap their BLE work in
  ``async with ble_lock.exclusive():`` — pausers fire on entry,
  resumers fire on exit (success OR failure).
* The internal ``asyncio.Lock`` makes overlapping ``exclusive()``
  acquisitions queue rather than race; in practice the FSM never
  runs two BLE measurements concurrently (MEASURING_VITALS ends
  before MEASURING_ANTHRO begins) but the lock is defence in
  depth.

This module has zero hardware dependencies and is fully testable
without bleak / BlueZ — the registered pause/resume callbacks are
plain coroutines.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog


PauseCallback = Callable[[], Awaitable[None]]
ResumeCallback = Callable[[], Awaitable[None]]


class BleAdapterLock:
    """Serialise BLE adapter access between scan-style and connect-style sensors.

    Single instance per kiosk runtime, constructed in
    :func:`create_all_sensors` and injected into both the Xiaomi
    scale and the Omron BP sensor. Tests can construct one
    directly.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pausers: list[PauseCallback] = []
        self._resumers: list[ResumeCallback] = []
        self._logger = structlog.get_logger("ble_lock")

    def register_scanner(self, *, pause: PauseCallback, resume: ResumeCallback) -> None:
        """Register a continuous-scan sensor's pause/resume hooks.

        Called by ``XiaomiScaleSensor.start()`` after its
        ``BleakScanner`` is up. The lock invokes ``pause`` before
        every ``exclusive()`` block and ``resume`` after, so the
        adapter is exclusively available to the connector during
        the block.
        """
        self._pausers.append(pause)
        self._resumers.append(resume)

    def unregister_scanner(
        self, *, pause: PauseCallback, resume: ResumeCallback
    ) -> None:
        """Drop a scanner's hooks — call from the sensor's ``stop()``."""
        try:
            self._pausers.remove(pause)
        except ValueError:
            pass
        try:
            self._resumers.remove(resume)
        except ValueError:
            pass

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Acquire the BLE adapter exclusively for a directed connect.

        On entry: acquire the internal lock, then run every
        registered ``pause`` callback in registration order.
        On exit: run every registered ``resume`` callback in reverse
        registration order, then release the lock — even if the
        protected block raises.

        Pause / resume failures are logged but not propagated; a
        scanner that can't be paused must not block the connector
        from at least trying.
        """
        async with self._lock:
            for pause in list(self._pausers):
                try:
                    await pause()
                except Exception as exc:
                    self._logger.warning(
                        "ble_lock.pause_failed",
                        error=type(exc).__name__,
                        error_msg=str(exc),
                    )
            try:
                yield
            finally:
                for resume in reversed(list(self._resumers)):
                    try:
                        await resume()
                    except Exception as exc:
                        self._logger.warning(
                            "ble_lock.resume_failed",
                            error=type(exc).__name__,
                            error_msg=str(exc),
                        )
