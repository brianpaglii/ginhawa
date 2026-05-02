"""Async pub/sub event bus behaviour."""

from __future__ import annotations

import pytest

from ginhawa_kiosk.fsm import EventBus, RfidScanned

from .conftest import utc_now_iso  # re-use shared helpers


# Verifies the bus dispatches a published event to every subscribed
# handler in registration order. Mortality: would fail if the bus
# stopped iterating after the first handler, or if it changed the
# call order (the FSM relies on consistent dispatch ordering).
@pytest.mark.asyncio
async def test_publish_invokes_subscribers_in_registration_order() -> None:
    bus = EventBus()
    calls: list[str] = []

    async def first(_: RfidScanned) -> None:
        calls.append("first")

    async def second(_: RfidScanned) -> None:
        calls.append("second")

    bus.subscribe(RfidScanned, first)
    bus.subscribe(RfidScanned, second)
    await bus.publish(RfidScanned(uid="CARD_BUS_PROBE"))

    assert calls == ["first", "second"]


# Verifies a handler that raises does NOT cancel sibling handlers.
# The bus is the offline-resilience choke point — one bad subscriber
# (e.g., a sensor adapter parse error) must not knock the audit-
# writing handler off the bus. Would fail if the bus stopped
# iterating on the first exception, or if it propagated the
# exception to publish()'s caller (which would cascade into the
# event source's loop).
@pytest.mark.asyncio
async def test_publish_isolates_handler_failures() -> None:
    class FakeLogger:
        def __init__(self) -> None:
            self.errors: list[tuple[str, dict[str, object]]] = []

        def error(self, event: str, **kwargs: object) -> None:
            self.errors.append((event, kwargs))

    logger = FakeLogger()
    bus = EventBus(logger=logger)
    survived: list[str] = []

    async def bad(_: RfidScanned) -> None:
        raise RuntimeError("simulated parse failure")

    async def good(_: RfidScanned) -> None:
        survived.append("good")

    bus.subscribe(RfidScanned, bad)
    bus.subscribe(RfidScanned, good)
    await bus.publish(RfidScanned(uid="CARD_BAD_HANDLER"))

    assert survived == ["good"]
    assert any(e[0] == "event_bus.handler_failed" for e in logger.errors)


# Sanity: the shared helper module is exercised via this import so
# the conftest module's own coverage is recorded.
def test_utc_now_iso_returns_string() -> None:
    value = utc_now_iso()
    assert isinstance(value, str) and len(value) >= 10
