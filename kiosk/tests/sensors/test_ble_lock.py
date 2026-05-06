"""BleAdapterLock — coordination between scan-style and connect-style BLE sensors.

The 2026-05-06 bench surfaced this contract: when the Xiaomi scale's
continuous BleakScanner runs, the Omron BP cuff's BleakClient.connect()
hits ``[org.bluez.Error.InProgress]`` because BlueZ can't run a scan
and a directed connect on the same hci0 adapter at the same time.
``BleAdapterLock`` pauses the scanner around the connect — these
tests pin the contract without booting any actual BLE.
"""

from __future__ import annotations

import asyncio

import pytest

from ginhawa_kiosk.sensors.ble_lock import BleAdapterLock


# Verifies a registered scanner's pause runs on entry and resume
# runs on exit of an exclusive() block.
# Mortality: would fail if either callback failed to fire — exactly
# the regression that brought back the InProgress storm.
@pytest.mark.asyncio
async def test_exclusive_runs_pause_then_yields_then_resume() -> None:
    events: list[str] = []

    async def pause() -> None:
        events.append("pause")

    async def resume() -> None:
        events.append("resume")

    lock = BleAdapterLock()
    lock.register_scanner(pause=pause, resume=resume)

    async with lock.exclusive():
        events.append("inside")

    assert events == ["pause", "inside", "resume"]


# Verifies resume STILL fires when the protected block raises — the
# Xiaomi scanner must come back even if the Omron connect crashed.
# Mortality: would fail if the scanner stayed paused forever after
# a single failed BP connect, blocking weight readings until restart.
@pytest.mark.asyncio
async def test_resume_fires_even_when_block_raises() -> None:
    events: list[str] = []

    async def pause() -> None:
        events.append("pause")

    async def resume() -> None:
        events.append("resume")

    lock = BleAdapterLock()
    lock.register_scanner(pause=pause, resume=resume)

    with pytest.raises(RuntimeError):
        async with lock.exclusive():
            events.append("inside")
            raise RuntimeError("simulated connect failure")

    assert events == ["pause", "inside", "resume"]


# Verifies multiple registered scanners are all paused on entry and
# resumed on exit. No scanners is also a valid configuration (the
# bench script's standalone run, or unit tests that don't register).
# Mortality: would fail if a future scanner-style sensor (e.g., a
# new BLE-advertised vital) silently kept running through BP connects.
@pytest.mark.asyncio
async def test_multiple_scanners_all_paused_and_resumed() -> None:
    events: list[str] = []

    async def make_pause(name: str):
        async def _p() -> None:
            events.append(f"pause:{name}")

        return _p

    async def make_resume(name: str):
        async def _r() -> None:
            events.append(f"resume:{name}")

        return _r

    lock = BleAdapterLock()
    lock.register_scanner(
        pause=await make_pause("xiaomi"), resume=await make_resume("xiaomi")
    )
    lock.register_scanner(
        pause=await make_pause("future"), resume=await make_resume("future")
    )

    async with lock.exclusive():
        events.append("inside")

    # Pause runs in registration order; resume runs in reverse so the
    # first-registered scanner is the last to resume — a defensive
    # ordering for any future scanner that depends on a sibling.
    assert events == [
        "pause:xiaomi",
        "pause:future",
        "inside",
        "resume:future",
        "resume:xiaomi",
    ]


# Verifies overlapping exclusive() acquisitions queue rather than
# race. The FSM never invokes BP concurrent with BP, but the lock
# is defence-in-depth.
# Mortality: would fail if a wiring bug let two BP connects run at
# once — corrupting the scanner pause-count and leaving it stopped.
@pytest.mark.asyncio
async def test_concurrent_exclusive_serialises() -> None:
    pause_count = 0
    resume_count = 0
    in_flight = 0
    max_concurrent = 0

    async def pause() -> None:
        nonlocal pause_count
        pause_count += 1

    async def resume() -> None:
        nonlocal resume_count
        resume_count += 1

    lock = BleAdapterLock()
    lock.register_scanner(pause=pause, resume=resume)

    async def task(name: str) -> None:
        nonlocal in_flight, max_concurrent
        async with lock.exclusive():
            in_flight += 1
            max_concurrent = max(max_concurrent, in_flight)
            await asyncio.sleep(0)  # yield the loop
            in_flight -= 1

    await asyncio.gather(task("a"), task("b"), task("c"))

    assert max_concurrent == 1
    # Each acquisition fires one pause and one resume — no nested
    # pauses, no leftover-paused state.
    assert pause_count == 3
    assert resume_count == 3


# Verifies a scanner that fails to pause does NOT prevent the
# exclusive block from running. Symptom we want: log + continue.
# Mortality: would fail if a flaky pause crashed every BP connect.
@pytest.mark.asyncio
async def test_pause_failure_is_logged_not_propagated() -> None:
    block_ran = False

    async def pause() -> None:
        raise OSError("simulated pause failure")

    async def resume() -> None:
        pass

    lock = BleAdapterLock()
    lock.register_scanner(pause=pause, resume=resume)

    async with lock.exclusive():
        nonlocal_marker = "ran"
        block_ran = nonlocal_marker == "ran"

    assert block_ran


# Verifies a scanner that fails to RESUME doesn't stop the lock from
# returning — the BP path completes; the scanner stays paused (which
# is recoverable on next sensor restart) but the kiosk doesn't hang.
# Mortality: would fail if a flaky resume left the lock acquired and
# blocked the next BP attempt forever.
@pytest.mark.asyncio
async def test_resume_failure_is_logged_not_propagated() -> None:
    async def pause() -> None:
        pass

    async def resume() -> None:
        raise OSError("simulated resume failure")

    lock = BleAdapterLock()
    lock.register_scanner(pause=pause, resume=resume)

    async with lock.exclusive():
        pass

    # If we got here, the lock released cleanly. Confirm it can be
    # re-acquired (the lock isn't deadlocked).
    async with lock.exclusive():
        pass


# Verifies unregister_scanner drops a scanner from the rotation.
# Mortality: would fail if scanner stop() didn't clean up its hooks
# and we kept calling pause/resume on a stopped scanner.
@pytest.mark.asyncio
async def test_unregister_scanner_drops_callbacks() -> None:
    pause_calls = 0

    async def pause() -> None:
        nonlocal pause_calls
        pause_calls += 1

    async def resume() -> None:
        pass

    lock = BleAdapterLock()
    lock.register_scanner(pause=pause, resume=resume)
    async with lock.exclusive():
        pass
    assert pause_calls == 1

    lock.unregister_scanner(pause=pause, resume=resume)
    async with lock.exclusive():
        pass
    # No additional pause call — the scanner is gone from the rotation.
    assert pause_calls == 1


# Verifies an exclusive block with no scanners registered still works.
# That's the configuration the bench script sees: the Xiaomi sensor
# isn't running, so the lock has nothing to pause but the BP path
# should still proceed.
# Mortality: would fail if the lock required at least one scanner to
# be registered, breaking the bench's solo BP path.
@pytest.mark.asyncio
async def test_exclusive_with_no_scanners_works() -> None:
    lock = BleAdapterLock()
    inside = False
    async with lock.exclusive():
        inside = True
    assert inside
