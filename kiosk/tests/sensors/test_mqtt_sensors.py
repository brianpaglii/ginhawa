"""MQTT sensor subscriber: mock + topic-routing logic."""

from __future__ import annotations

import json

import pytest
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
    set_device_config(db_session, "kiosk_device_id", _DEVICE_ID)
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
    set_device_config(db_session, "kiosk_device_id", _DEVICE_ID)
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
    set_device_config(db_session, "kiosk_device_id", _DEVICE_ID)
    sub = MqttSensorSubscriber(bus, db_session)

    topic = f"ginhawa/kiosk/{_DEVICE_ID}/sensors/blood_alcohol"
    body = json.dumps({"value": 0.04, "unit": "mg/dL"}).encode()
    await sub._handle_message_payload(topic, body)

    assert captured_measurements == []


# Verifies start() refuses to run if the kiosk_device_id isn't
# configured. The subscriber needs the device_id to build its topic
# filter; without it, subscribing to ``ginhawa/kiosk/None/...`` would
# silently miss every real ESP32 message.
@pytest.mark.asyncio
async def test_mqtt_subscriber_raises_on_missing_device_id(
    bus: EventBus, db_session: Session
) -> None:
    sub = MqttSensorSubscriber(bus, db_session)
    with pytest.raises(SensorUnavailable, match="kiosk_device_id"):
        await sub.start()
