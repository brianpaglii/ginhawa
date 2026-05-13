"""Async pub/sub event bus.

Sensors and the GUI publish typed events to the bus; the FSM (and
anything else interested) subscribes by event type. The bus
decouples the FSM from sensor implementations — the FSM does not
know whether an :class:`RfidScanned` event came from the MFRC522
adapter, the mock adapter, or a test fixture.

Semantics:

* No reordering. No priorities. Events are dispatched to subscribers
  in arrival order (Python asyncio task queue ordering).
* Handlers are async functions. Synchronous handlers are not
  supported — wrap them in an async shim if you need that.
* If a handler raises, the bus logs the failure but does NOT cancel
  other handlers for the same event. One sensor's parse error must
  not knock the audit-writing handler off the bus.

# BpMeasurementRequested is published by the FSM when it transitions
# into the BP-measurement sub-state of MEASURING_VITALS. The wiring
# from FSM → this event lands in the GUI/sensor-coordinator prompt
# (Phase 2 Prompt 8). Until then, BP measurement can be triggered
# in tests by publishing this event directly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel


class Event(BaseModel):
    """Base class for all bus-routed events. Subclass it to define a
    new event type with typed fields."""


_E = TypeVar("_E", bound=Event)
Handler = Callable[[Any], Awaitable[None]]


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class RfidScanned(Event):
    uid: str


class CitizenIdentified(Event):
    """Fired by the citizen-lookup service after an RFID scan.

    ``citizen_id`` is None when the RFID UID does not resolve to a
    known citizen — the FSM uses that to decide between the
    REGISTERING and MENU/CONSENT_VERIFICATION paths.
    """

    citizen_id: str | None


class ConsentGiven(Event):
    pass


class ConsentRefused(Event):
    pass


class PathSelected(Event):
    path: str  # 'vitals' | 'anthropometric' | 'full'


class MeasurementProposed(Event):
    """A measurement payload coming off a sensor adapter.

    Carries the full sensor reading so the wiring layer can run
    ``validate_measurement`` and persist a single Measurement row
    with the validation-service's verdict — one write per
    measurement, not two. ``claimed_is_valid`` is the kiosk's
    *belief* (typically ``True`` from the sensor adapter that
    parsed the BLE/MQTT payload); the validation service has the
    final say on the persisted ``is_valid`` and may override it
    with a ``validation_notes`` string.

    ``validation_notes`` is producer-supplied context for cases
    where the publisher already knows the row is invalid AND wants
    to preserve a specific reason — e.g., the FSM seeding an
    "offline placeholder" row when a sensor's transport (MQTT,
    BLE) is down at the start of a measuring state. When set in
    combination with ``claimed_is_valid=False``, the persist path
    skips the unit/range validator (which would clobber the
    reason with a "unit must be …" string) and writes the
    producer's notes verbatim.

    There is no separate ``MeasurementCaptured`` event today. If a
    future GUI subscriber needs notification *after* persistence
    (e.g., real-time on-screen display of the just-saved row), add
    a second event class then — don't pre-emptively split the
    surface.
    """

    measurement_type: str
    value: float
    unit: str
    source_device: str
    claimed_is_valid: bool
    validation_notes: str | None = None


class LiveTemperatureUpdate(Event):
    """Live preview value off the MLX90640's MQTT stream.

    Distinct from :class:`MeasurementProposed`: the MLX90640 publishes
    continuously (every ~3–5 s) regardless of whether the citizen has
    positioned the thermal sensor on their forehead. Persisting every
    publish would silently lock in a room-temperature reading taken
    before the citizen lifted the sensor. So the MQTT subscriber emits
    THIS event for the temperature stream, which the MEASURING_VITALS
    screen consumes for a live "Current: 36.7 °C" display only. A
    citizen tap on the screen's Capture button then re-emits the
    held value as a :class:`MeasurementProposed` for persistence.
    """

    value: float
    unit: str  # "C" — the unit string published by the ESP32 firmware
    captured_at: str  # ISO 8601; when ESP32-A published OR kiosk-stamped


class MeasurementPathComplete(Event):
    pass


class PrintRequested(Event):
    pass


class PrintComplete(Event):
    success: bool
    printed_status: str


class FinishWithoutPrinting(Event):
    pass


class PaperOutDetected(Event):
    pass


class ErrorOccurred(Event):
    reason: str


class TimeoutFired(Event):
    pass


class Acknowledge(Event):
    pass


class BpMeasurementRequested(Event):
    """Fired by the FSM when entering MEASURING_VITALS to ask the
    Omron BP cuff sensor to take a reading. The sensor connects,
    awaits one notification, parses, publishes MeasurementProposed
    events for systolic / diastolic / pulse, then disconnects."""


class BpMeasurementRequestCancelled(Event):
    """Fired by the GUI when the FSM exits MEASURING_VITALS for any
    reason (cancel, change-language, error, or REPORT after the BP
    triple was published). The Omron BP sensor's request handler
    treats this as the SOLE give-up signal: time-based budgets
    inside the handler were removed because the user is the natural
    bound — they can cancel — and the wall-clock window for "citizen
    fumbling with the cuff" is too unpredictable to encode in a
    constant. Without this event the handler would loop forever on
    a session whose user walked away."""


class SessionResetForSensors(Event):
    """Signal that a session has ended (or a new one is starting).

    Sensors that maintain per-session state (e.g., the Xiaomi
    scale's stability + lock gate, which captures one weight per
    session and then suppresses further readings) subscribe to
    this event and clear that state on receipt. The main window
    publishes it on state transitions into ``IDLE`` and
    ``LANGUAGE_SELECT`` — IDLE covers normal end / aborted /
    error returns, LANGUAGE_SELECT covers a fresh session start
    immediately after RFID identification.
    """


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class EventBus:
    """In-process async pub/sub. One instance per kiosk runtime."""

    def __init__(self, *, logger: Any | None = None) -> None:
        self._subs: dict[type[Event], list[Handler]] = defaultdict(list)
        self._logger = logger or _default_logger()

    def subscribe(
        self, event_type: type[_E], handler: Callable[[_E], Awaitable[None]]
    ) -> None:
        """Register a handler for events of ``event_type``. Multiple handlers
        per type are allowed; they are invoked in registration order."""
        self._subs[event_type].append(handler)

    async def publish(self, event: Event) -> None:
        """Dispatch ``event`` to all handlers subscribed to its type.

        A handler that raises is logged and skipped — the bus does
        NOT propagate the exception. The remaining handlers still
        run. This is the offline-resilient pattern: one bad
        subscriber must not take the whole kiosk down.
        """
        for handler in list(self._subs[type(event)]):
            try:
                await handler(event)
            except Exception as exc:
                # Pass the event class name under "event_type" rather
                # than "event" because structlog uses the first
                # positional as the event-name kwarg internally; a
                # collision raises TypeError on common logger stubs.
                self._logger.error(
                    "event_bus.handler_failed",
                    event_type=type(event).__name__,
                    handler=getattr(handler, "__qualname__", repr(handler)),
                    error=str(exc),
                )


def _default_logger() -> Any:  # pragma: no cover - lazy structlog init
    import structlog

    return structlog.get_logger("event_bus")
