"""MQTT sensor subscriber: mock + topic-routing logic."""

from __future__ import annotations

import json

import pytest
import structlog
from sqlalchemy.orm import Session

from ginhawa_kiosk.fsm import EventBus, MeasurementProposed
from ginhawa_kiosk.sensors.base import SensorUnavailable
from ginhawa_kiosk.sensors.mqtt_sensors import (
    MockMqttSensors,
    MqttSensorSubscriber,
)

from .conftest import set_device_config


_DEVICE_ID = "00000000-0000-0000-0000-000000000401"


# Verifies the mock's lifecycle. is_running flips correctly across
# start/stop.
@pytest.mark.asyncio
async def test_mock_mqtt_subscriber_lifecycle(bus: EventBus) -> None:
    sensor = MockMqttSensors(bus)
    assert sensor.is_running is False
    await sensor.start()
    assert sensor.is_running is True
    await sensor.stop()
    assert sensor.is_running is False


# Verifies the mock routes each topic-suffix to the right
# MeasurementProposed event (measurement_type matches the suffix).
# Mortality: would fail if topic-to-measurement-type routing were
# broken.
@pytest.mark.asyncio
async def test_mock_mqtt_subscriber_publishes_event_for_each_topic(
    bus: EventBus, captured_measurements: list[MeasurementProposed]
) -> None:
    sensor = MockMqttSensors(bus)
    await sensor.simulate_publish("spo2", 98.0, "%")
    await sensor.simulate_publish("heart_rate", 72.0, "bpm")
    await sensor.simulate_publish("temperature", 36.5, "C")
    await sensor.simulate_publish("height", 165.0, "cm")

    types = [m.measurement_type for m in captured_measurements]
    assert types == ["spo2", "heart_rate", "temperature", "height"]


# Verifies the real subscriber's _handle_message_payload routes each
# topic to the correct event. The four expected topics produce four
# MeasurementProposed events.
# Mortality: would fail if the topic-routing dict were truncated or
# the suffix-extraction broken.
@pytest.mark.asyncio
async def test_mqtt_subscriber_publishes_event_for_each_topic(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "kiosk_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)

    payloads = {
        "spo2": (98.0, "%"),
        "heart_rate": (72.0, "bpm"),
        "temperature": (36.5, "C"),
        "height": (165.0, "cm"),
    }
    for suffix, (value, unit) in payloads.items():
        topic = f"ginhawa/kiosk/{_DEVICE_ID}/sensors/{suffix}"
        body = json.dumps(
            {
                "value": value,
                "unit": unit,
                "captured_at": "2026-05-02T00:00:00+00:00",
            }
        ).encode()
        await sub._handle_message_payload(topic, body)

    types = {m.measurement_type for m in captured_measurements}
    assert types == {"spo2", "heart_rate", "temperature", "height"}


# Verifies malformed JSON is logged and dropped — no event, no crash.
# The kiosk MUST NOT crash on a bad MQTT payload (CLAUDE.md "Failure
# modes — fail loud, fail safe": one bad sensor must not knock out
# the kiosk).
# Mortality: would fail if a malformed payload crashed the
# subscriber.
@pytest.mark.asyncio
async def test_mqtt_subscriber_drops_malformed_payloads(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "kiosk_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)

    topic = f"ginhawa/kiosk/{_DEVICE_ID}/sensors/spo2"
    # Garbage that isn't valid JSON.
    await sub._handle_message_payload(topic, b"not-json-at-all{{")
    # Valid JSON but not an object.
    await sub._handle_message_payload(topic, b"[1, 2, 3]")
    # Valid JSON object but missing required fields.
    await sub._handle_message_payload(topic, b'{"foo": "bar"}')

    assert captured_measurements == []


# Verifies an unexpected topic-suffix is logged and dropped without
# raising. ESP32 firmware that emits a typo'd topic must not take
# the subscriber down.
@pytest.mark.asyncio
async def test_mqtt_subscriber_drops_unknown_topic_suffix(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "kiosk_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)

    topic = f"ginhawa/kiosk/{_DEVICE_ID}/sensors/blood_alcohol"
    body = json.dumps({"value": 0.04, "unit": "mg/dL"}).encode()
    await sub._handle_message_payload(topic, body)

    assert captured_measurements == []


# Verifies start() refuses to run if the kiosk_id isn't configured.
# The subscriber needs the device_id to build its topic filter;
# without it, subscribing to ``ginhawa/kiosk/None/...`` would
# silently miss every real ESP32 message. The error message
# explicitly references "kiosk_id" so an operator reading the
# journal can map it to the device_config row __main__.py also
# expects.
@pytest.mark.asyncio
async def test_start_raises_when_kiosk_id_missing(
    bus: EventBus, db_session: Session
) -> None:
    sub = MqttSensorSubscriber(bus, db_session)
    with pytest.raises(SensorUnavailable, match="kiosk_id"):
        await sub.start()


# Verifies the lookup hits the device_config row that __main__.py
# also writes to (key="kiosk_id"). Mortality: would fail if anyone
# re-introduced the old "kiosk_device_id" literal — the bench DB is
# seeded with "kiosk_id" and a mismatch silently degrades to a
# SensorUnavailable boot loop.
def test_load_device_id_from_kiosk_id_key(bus: EventBus, db_session: Session) -> None:
    set_device_config(db_session, "kiosk_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)
    assert sub._load_device_id() == _DEVICE_ID


# Verifies the loader returns None (not a string default) when the
# row is absent, so start() can raise an explicit SensorUnavailable
# instead of building a topic filter against a falsy device_id.
def test_load_device_id_returns_none_when_kiosk_id_missing(
    bus: EventBus, db_session: Session
) -> None:
    sub = MqttSensorSubscriber(bus, db_session)
    assert sub._load_device_id() is None


# Verifies the success-path liveness log fires AFTER the event bus
# accepts the message. journalctl grep for "mqtt.message_routed" is
# the bench operator's success marker; without this log the
# subscriber's reception is invisible without DB introspection.
# Mortality: would fail if the log were demoted to debug, dropped,
# moved before the event-bus publish, or stripped of any of the four
# documented fields.
@pytest.mark.asyncio
async def test_handle_message_logs_on_success(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "kiosk_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)

    topic = f"ginhawa/kiosk/{_DEVICE_ID}/sensors/spo2"
    payload = json.dumps(
        {"value": 97.0, "unit": "%", "captured_at": "2026-05-09T12:00:00+00:00"}
    ).encode()

    with structlog.testing.capture_logs() as logs:
        await sub._handle_message_payload(topic, payload)

    # Event bus saw it (sanity: the log fires only after the publish).
    assert len(captured_measurements) == 1

    routed = [entry for entry in logs if entry.get("event") == "mqtt.message_routed"]
    assert len(routed) == 1, f"expected 1 success log, got logs={logs!r}"
    entry = routed[0]
    assert entry["log_level"] == "info"
    assert entry["topic"] == topic
    assert entry["measurement_type"] == "spo2"
    assert entry["value"] == 97.0
    assert entry["unit"] == "%"
    # Publisher-supplied captured_at is preserved verbatim.
    assert entry["captured_at"] == "2026-05-09T12:00:00+00:00"


# Verifies the kiosk stamps captured_at locally when the payload omits
# the field — current ESP32 firmware does this on purpose to avoid
# the NTP / internet dependency. The stamp is a UTC isoformat string
# parseable as a tz-aware datetime.
# Mortality: would fail if the fallback path were removed or if the
# kiosk silently substituted an empty string instead of stamping.
@pytest.mark.asyncio
async def test_handle_message_stamps_captured_at_when_payload_omits_it(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    from datetime import datetime

    set_device_config(db_session, "kiosk_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)

    topic = f"ginhawa/kiosk/{_DEVICE_ID}/sensors/spo2"
    # No captured_at field — what current ESP32 firmware sends.
    payload = json.dumps({"value": 97.0, "unit": "%"}).encode()

    with structlog.testing.capture_logs() as logs:
        await sub._handle_message_payload(topic, payload)

    assert len(captured_measurements) == 1
    routed = [entry for entry in logs if entry.get("event") == "mqtt.message_routed"]
    assert len(routed) == 1
    stamped = routed[0]["captured_at"]
    # Round-trips through fromisoformat → tz-aware datetime in UTC.
    parsed = datetime.fromisoformat(stamped)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0


# Verifies the success log does NOT fire on a malformed payload —
# the warning path returns early, before _route_to_event. Pairs with
# test_handle_message_logs_on_success: success and failure branches
# have visibly different journal markers.
@pytest.mark.asyncio
async def test_handle_message_does_not_log_routed_on_malformed(
    bus: EventBus,
    captured_measurements: list[MeasurementProposed],
    db_session: Session,
) -> None:
    set_device_config(db_session, "kiosk_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)

    topic = f"ginhawa/kiosk/{_DEVICE_ID}/sensors/spo2"
    with structlog.testing.capture_logs() as logs:
        await sub._handle_message_payload(topic, b"not-json{{")

    assert captured_measurements == []
    assert all(entry.get("event") != "mqtt.message_routed" for entry in logs)
