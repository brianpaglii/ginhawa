"""Continuous-capture diagnostic / demo CLI.

A long-running operator tool that subscribes to the kiosk's event
bus and prints / logs every measurement it receives, indefinitely,
until interrupted with Ctrl-C. Used during commissioning, hardware
testing, demos, and field-debug sessions.

What this is NOT
================

* **Not part of the production kiosk runtime.** The kiosk's normal
  GUI / FSM path under ``ginhawa_kiosk:main`` is the only path that
  produces audited, citizen-attributed measurements. This tool is a
  read-only observer of sensor events.
* **Not a data-collection tool.** It does not write anything to the
  citizens / sessions / measurements / audit_log tables. There is no
  consent capture, no session structure, no audit attribution. Do
  NOT use it to record real patient data — the kiosk's normal flow
  is the only legitimate path for that.
* **Not a replacement for the kiosk's structured tests.** The unit
  / integration suites are still the right place to verify that
  behaviour holds. This tool is a hardware probe, not a test runner.

Example invocations
===================

::

    # Capture from all sensors, log to default location
    KIOSK_DB_PATH=/var/lib/ginhawa/kiosk.db \\
    KIOSK_DB_KEY="$KIOSK_DB_KEY"            \\
    uv run python -m ginhawa_kiosk.scripts.continuous_capture

    # Only RFID, no log file (stdout only, useful for quick card-reading checks)
    uv run python -m ginhawa_kiosk.scripts.continuous_capture \\
        --sensors rfid --no-log-file

    # On a developer laptop with no hardware
    MOCK_HARDWARE=true uv run python -m ginhawa_kiosk.scripts.continuous_capture
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

import structlog

from ..core.config import Settings, get_settings
from ..db.session import create_engine_for_kiosk, make_session_factory
from ..fsm.event_bus import (
    BpMeasurementRequested,
    EventBus,
    MeasurementProposed,
    RfidScanned,
)
from ..sensors import (
    Sensor,
    create_omron_bp,
    create_rfid_reader,
    create_xiaomi_scale,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LOG_FILE = Path.home() / ".ginhawa-continuous-capture.jsonl"

_SENSOR_GROUPS: dict[str, tuple[str, ...]] = {
    "rfid": ("rfid",),
    "xiaomi": ("xiaomi",),
    "omron": ("omron",),
    "all": ("rfid", "xiaomi", "omron"),
}


# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Args:
    sensors: str
    log_file: Path | None
    verbose: bool
    bp_prompt: bool


def _parse_args(argv: list[str] | None = None) -> _Args:
    parser = argparse.ArgumentParser(
        prog="ginhawa-continuous-capture",
        description=(
            "Long-running diagnostic / demo CLI that prints every kiosk "
            "sensor event to stdout (and optionally to a JSONL log "
            "file). Read-only — does not write to the kiosk database."
        ),
    )
    parser.add_argument(
        "--sensors",
        choices=sorted(_SENSOR_GROUPS),
        default="all",
        help="which sensors to enable (default: all)",
    )
    log_group = parser.add_mutually_exclusive_group()
    log_group.add_argument(
        "--log-file",
        type=Path,
        default=_DEFAULT_LOG_FILE,
        help=f"JSONL log path (default: {_DEFAULT_LOG_FILE})",
    )
    log_group.add_argument(
        "--no-log-file",
        action="store_true",
        help="disable file logging; print to stdout only",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="set LOG_LEVEL=DEBUG for the structlog renderer",
    )
    parser.add_argument(
        "--no-bp-prompt",
        action="store_true",
        help=(
            "disable the interactive BP-trigger prompt; useful for "
            "headless runs or when a different process publishes "
            "BpMeasurementRequested"
        ),
    )
    ns = parser.parse_args(argv)
    log_file: Path | None = None if ns.no_log_file else ns.log_file
    return _Args(
        sensors=ns.sensors,
        log_file=log_file,
        verbose=ns.verbose,
        bp_prompt=not ns.no_bp_prompt,
    )


# ---------------------------------------------------------------------------
# Capture state
# ---------------------------------------------------------------------------


@dataclass
class _CaptureState:
    counts: Counter[str]
    started_at: float
    log_handle: IO[str] | None

    def increment(self, event_name: str) -> None:
        self.counts[event_name] += 1


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def _format_rfid_stdout(event: RfidScanned) -> str:
    return f"[{_stamp()}] RfidScanned: uid={event.uid}"


def _format_measurement_stdout(event: MeasurementProposed) -> str:
    return (
        f"[{_stamp()}] MeasurementProposed: "
        f"type={event.measurement_type} "
        f"value={event.value} "
        f"unit={event.unit} "
        f"source={event.source_device} "
        f"valid={event.claimed_is_valid}"
    )


def _jsonl_record(event_name: str, fields: dict[str, Any]) -> str:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_name,
        **fields,
    }
    return json.dumps(payload, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------


def _make_rfid_handler(
    state: _CaptureState, stdout: IO[str]
) -> Callable[[RfidScanned], Awaitable[None]]:
    async def handler(event: RfidScanned) -> None:
        state.increment("RfidScanned")
        print(_format_rfid_stdout(event), file=stdout, flush=True)
        if state.log_handle is not None:
            state.log_handle.write(
                _jsonl_record("RfidScanned", {"uid": event.uid}) + "\n"
            )
            state.log_handle.flush()

    return handler


def _make_measurement_handler(
    state: _CaptureState, stdout: IO[str]
) -> Callable[[MeasurementProposed], Awaitable[None]]:
    async def handler(event: MeasurementProposed) -> None:
        state.increment("MeasurementProposed")
        print(_format_measurement_stdout(event), file=stdout, flush=True)
        if state.log_handle is not None:
            state.log_handle.write(
                _jsonl_record(
                    "MeasurementProposed",
                    {
                        "measurement_type": event.measurement_type,
                        "value": event.value,
                        "unit": event.unit,
                        "source_device": event.source_device,
                        "claimed_is_valid": event.claimed_is_valid,
                    },
                )
                + "\n"
            )
            state.log_handle.flush()

    return handler


# ---------------------------------------------------------------------------
# Sensor wiring
# ---------------------------------------------------------------------------


def _build_sensors(
    bus: EventBus,
    settings: Settings,
    db: Any,
    enabled: tuple[str, ...],
) -> dict[str, Sensor]:
    sensors: dict[str, Sensor] = {}
    if "rfid" in enabled:
        sensors["rfid"] = create_rfid_reader(bus, settings)
    if "xiaomi" in enabled:
        sensors["xiaomi"] = create_xiaomi_scale(bus, settings, db)
    if "omron" in enabled:
        sensors["omron"] = create_omron_bp(bus, settings, db)
    return sensors


# ---------------------------------------------------------------------------
# Interactive BP trigger
# ---------------------------------------------------------------------------


_BP_PROMPT = (
    "[bp] Press Enter to trigger a BP capture. "
    "When prompted, press the Bluetooth button on the cuff, then START."
)


async def _bp_trigger_loop(bus: EventBus, stdout: IO[str], stop: asyncio.Event) -> None:
    """Read one Enter press at a time from stdin, publish a
    BpMeasurementRequested for each. Runs the blocking ``input``
    on a thread so Ctrl-C still interrupts the main loop."""
    while not stop.is_set():
        print(_BP_PROMPT, file=stdout, flush=True)
        try:
            await asyncio.to_thread(input)
        except (EOFError, KeyboardInterrupt):
            return
        if stop.is_set():
            return
        print(
            f"[{_stamp()}] BpMeasurementRequested: triggering capture",
            file=stdout,
            flush=True,
        )
        await bus.publish(BpMeasurementRequested())


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def _run(
    args: _Args,
    *,
    settings: Settings | None = None,
    stdout: IO[str] | None = None,
    stop_after_seconds: float | None = None,
) -> int:
    """Async entry point. Tests pass ``stop_after_seconds`` so the
    loop terminates without sending SIGINT.

    ``settings`` is also injectable so tests don't have to mutate
    process env. Production calls pass ``None`` and let the
    ``Settings`` cache resolve from the env."""
    log = structlog.get_logger("continuous_capture")
    out = stdout or sys.stdout

    settings = settings or get_settings()
    enabled = _SENSOR_GROUPS[args.sensors]

    log_handle: IO[str] | None = None
    if args.log_file is not None:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = args.log_file.open("a", encoding="utf-8")

    state = _CaptureState(
        counts=Counter(), started_at=time.monotonic(), log_handle=log_handle
    )

    bus = EventBus()
    bus.subscribe(RfidScanned, _make_rfid_handler(state, out))
    bus.subscribe(MeasurementProposed, _make_measurement_handler(state, out))

    # Build the database session factory only as far as the sensors
    # require — they read device_config rows at start(). We commit
    # nothing.
    engine = create_engine_for_kiosk(settings.KIOSK_DB_PATH, settings.KIOSK_DB_KEY)
    session_factory = make_session_factory(engine)
    db = session_factory()

    try:
        sensors = _build_sensors(bus, settings, db, enabled)
    except Exception as exc:
        log.warning("continuous_capture.sensor_build_failed", error=str(exc))
        db.close()
        engine.dispose()
        if log_handle is not None:
            log_handle.close()
        return 1

    started: list[Sensor] = []
    try:
        for name, sensor in sensors.items():
            try:
                await sensor.start()
                started.append(sensor)
                print(f"[{_stamp()}] sensor.{name}: started", file=out, flush=True)
            except Exception as exc:
                log.warning(
                    "continuous_capture.sensor_start_failed",
                    sensor=name,
                    error=str(exc),
                )
                # Stop any sensors we already started so we don't leak threads.
                for s in started:
                    try:
                        await s.stop()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass
                db.close()
                engine.dispose()
                if log_handle is not None:
                    log_handle.close()
                return 1

        stop = asyncio.Event()
        bp_task: asyncio.Task[None] | None = None
        if args.bp_prompt and "omron" in enabled:
            bp_task = asyncio.create_task(_bp_trigger_loop(bus, out, stop))

        try:
            if stop_after_seconds is not None:
                await asyncio.sleep(stop_after_seconds)
            else:
                # Wait forever, until KeyboardInterrupt is delivered.
                await stop.wait()
        finally:
            stop.set()
            if bp_task is not None:
                bp_task.cancel()
                try:
                    await bp_task
                except (asyncio.CancelledError, KeyboardInterrupt):
                    pass

        return 0
    finally:
        for sensor in started:
            try:
                await sensor.stop()
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                log.warning("continuous_capture.sensor_stop_failed", error=str(exc))
        db.close()
        engine.dispose()
        _print_summary(state, out)
        if log_handle is not None:
            log_handle.close()


def _print_summary(state: _CaptureState, stdout: IO[str]) -> None:
    duration = time.monotonic() - state.started_at
    print("", file=stdout)
    print(f"[{_stamp()}] === capture summary ===", file=stdout)
    print(f"  duration_seconds: {duration:.1f}", file=stdout)
    if not state.counts:
        print("  events captured: (none)", file=stdout)
    else:
        for event_name, count in sorted(state.counts.items()):
            print(f"  {event_name}: {count}", file=stdout)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI surface
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        # Re-entry point: asyncio's run() raises KeyboardInterrupt out of
        # the loop. The cleanup happens in _run's finally block before
        # we get here, so just return success.
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI surface
    sys.exit(main())
