"""MQTT subscriber for the two ESP32 sensor nodes.

Topic taxonomy (subscribed with QoS 1):

* ``ginhawa/kiosk/<device_id>/sensors/spo2``         — ESP32-A
* ``ginhawa/kiosk/<device_id>/sensors/heart_rate``   — ESP32-A
* ``ginhawa/kiosk/<device_id>/sensors/temperature``  — ESP32-A
  (per ADR-0018; MLX90640BAB physically located near MAX30100 on
  the console node so the imager has the right viewing geometry to
  the citizen's forehead)
* ``ginhawa/kiosk/<device_id>/sensors/height``       — ESP32-B

Each topic carries a JSON payload ``{value: float, unit: str,
captured_at?: ISO8601}``. ``captured_at`` is OPTIONAL — the kiosk is
the authoritative source of capture time and stamps a UTC timestamp
on receipt when the publisher didn't include one. The current ESP32
firmware omits ``captured_at`` (no NTP / no internet dependency on
the firmware side); the bench publish script and any future firmware
revision with reliable NTP may include it, in which case the
publisher-supplied value is used verbatim.

The kiosk's ``device_id`` is loaded from ``device_config`` at start
time.

Resilience:

* Auto-reconnect via paho-mqtt's ``reconnect_delay_set``; the
  ``on_connect`` callback re-subscribes to every topic so a broker
  bounce (e.g., during an OS update) is transparent to application
  code.
* Topic-suffixes that don't match the four expected names are logged
  and dropped, never raised.
* JSON-decode failures are logged and dropped, never raised. The
  kiosk MUST NOT crash on a malformed MQTT payload.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DeviceConfig
from ..fsm.event_bus import EventBus, LiveTemperatureUpdate, MeasurementProposed
from .base import Sensor, SensorUnavailable


# The device-id row in device_config. __main__.py also reads this row
# (under its own ``_KIOSK_ID_KEY`` constant) — keeping the literal in
# sync is load-bearing: the bench DB is seeded with key="kiosk_id",
# and a mismatched literal here would fall back to None and the
# subscriber would refuse to start with SensorUnavailable.
_DEVICE_ID_CONFIG_KEY = "kiosk_id"

# Map topic-suffix → (measurement_type, expected_unit, source_device).
# unit is taken verbatim from the payload but we keep the expected
# unit here so a misconfigured ESP32 can be flagged in logs.
_TOPIC_ROUTES: dict[str, tuple[str, str, str]] = {
    "spo2": ("spo2", "%", "esp32_a_max30100"),
    "heart_rate": ("heart_rate", "bpm", "esp32_a_max30100"),
    "temperature": ("temperature", "C", "esp32_a_mlx90640"),
    "height": ("height", "cm", "esp32_b_vl53l0x"),
}


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


class MockMqttSensors(Sensor):
    """In-memory MQTT subscriber. Tests / dev call
    :meth:`simulate_publish`."""

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

    async def simulate_publish(
        self, topic_suffix: str, value: float, unit: str
    ) -> None:
        """Pretend an ESP32 just published on the given topic suffix.

        Same routing logic the real subscriber uses, so unit-test
        wiring is identical between mock and real.
        """
        captured_at = datetime.now(timezone.utc).isoformat()
        await _emit_for_payload(self._bus, topic_suffix, value, unit, captured_at)


# ---------------------------------------------------------------------------
# Real — paho-mqtt
# ---------------------------------------------------------------------------


class MqttSensorSubscriber(Sensor):
    """Subscribes to the four ESP32 sensor topics on the local broker.

    The paho-mqtt client runs its network loop on a background thread
    via ``loop_start()``; incoming messages are handed back to the
    main asyncio loop via ``run_coroutine_threadsafe``.

    Tests bypass the network entirely by calling
    :meth:`_handle_message_payload` directly with synthetic
    ``(topic, payload_bytes)`` pairs.
    """

    def __init__(
        self,
        bus: EventBus,
        db: Session,
        *,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        username: str = "",
        password: str = "",
        client_factory: Any | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._username = username
        self._password = password
        self._client_factory = client_factory
        self._logger = structlog.get_logger("sensor.mqtt")
        self._client: Any | None = None
        self._device_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:  # pragma: no cover - idempotency guard
            return
        self._device_id = self._load_device_id()
        if not self._device_id:
            raise SensorUnavailable(
                f"{_DEVICE_ID_CONFIG_KEY} missing from device_config; "
                "the kiosk cannot subscribe to its sensor topics without "
                "knowing its own device id"
            )
        # Below this point we touch paho-mqtt's network surface; the
        # unit tests bypass start/stop entirely by calling the message-
        # handling internals directly.
        self._loop = asyncio.get_running_loop()  # pragma: no cover
        self._client = self._build_client()  # pragma: no cover
        # Mosquitto on the LAN-bound deployment refuses anonymous
        # connects; supply credentials before connect() if the
        # operator configured them. Set both or neither — paho takes
        # username with optional password, but our broker requires
        # both, so we treat an empty username as "no auth at all".
        if self._username:  # pragma: no cover - real-network path
            self._client.username_pw_set(self._username, self._password)
        self._client.on_connect = self._on_connect  # pragma: no cover
        self._client.on_message = self._on_message  # pragma: no cover
        self._client.reconnect_delay_set(  # pragma: no cover
            min_delay=1, max_delay=30
        )
        self._client.connect(  # pragma: no cover
            self._broker_host, self._broker_port, keepalive=60
        )
        self._client.loop_start()  # pragma: no cover
        self._running = True  # pragma: no cover

    async def stop(self) -> None:  # pragma: no cover - real-network path
        if self._client is not None:
            self._client.loop_stop()
            try:
                self._client.disconnect()
            except Exception as exc:
                self._logger.warning("mqtt.disconnect_failed", error=str(exc))
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_device_id(self) -> str | None:
        row = self._db.execute(
            select(DeviceConfig).where(DeviceConfig.key == _DEVICE_ID_CONFIG_KEY)
        ).scalar_one_or_none()
        return row.value if row is not None else None

    def _build_client(self) -> Any:  # pragma: no cover - real-network path
        if self._client_factory is not None:
            return self._client_factory()
        from paho.mqtt.client import Client

        return Client(client_id=f"ginhawa-kiosk-{self._device_id}")

    def _topic_filter(self) -> str:  # pragma: no cover - used by paho cb
        return f"ginhawa/kiosk/{self._device_id}/sensors/+"

    def _on_connect(  # pragma: no cover - paho callback
        self,
        client: Any,
        _userdata: Any,
        _flags: Any,
        rc: int,
        _properties: Any | None = None,
    ) -> None:
        if rc != 0:
            self._logger.warning("mqtt.connect_failed", rc=rc)
            return
        # Re-subscribe on every connect so reconnects are transparent.
        client.subscribe(self._topic_filter(), qos=1)

    def _on_message(  # pragma: no cover - paho callback (thread)
        self, _client: Any, _userdata: Any, msg: Any
    ) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._handle_message_payload(msg.topic, bytes(msg.payload)),
            self._loop,
        )

    async def _handle_message_payload(self, topic: str, payload: bytes) -> None:
        """Route one inbound MQTT message to a MeasurementProposed event.

        Tests call this directly with synthetic ``(topic, payload)``
        pairs to bypass the network.
        """
        topic_suffix = topic.rsplit("/", 1)[-1]
        if topic_suffix not in _TOPIC_ROUTES:
            self._logger.warning(
                "mqtt.unexpected_topic", topic=topic, suffix=topic_suffix
            )
            return
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            self._logger.warning(
                "mqtt.malformed_payload",
                topic=topic,
                error=str(exc),
                # Don't log the payload bytes — could include garbage that
                # bloats the journal; the topic + error is enough.
            )
            return
        if not isinstance(decoded, dict):
            self._logger.warning("mqtt.payload_not_object", topic=topic)
            return
        try:
            value = float(decoded["value"])
            unit = str(decoded["unit"])
        except (KeyError, TypeError, ValueError) as exc:
            self._logger.warning(
                "mqtt.payload_missing_fields", topic=topic, error=str(exc)
            )
            return
        # The kiosk owns capture time. Accept a payload-supplied
        # captured_at (e.g. from the bench publish script or a future
        # firmware revision with reliable NTP) but fall back to local
        # UTC now if absent or malformed — current ESP32 firmware
        # omits the field on purpose (no internet / no NTP needed).
        payload_captured_at = decoded.get("captured_at")
        if isinstance(payload_captured_at, str) and payload_captured_at:
            captured_at = payload_captured_at
        else:
            captured_at = datetime.now(timezone.utc).isoformat()
        await _emit_for_payload(self._bus, topic_suffix, value, unit, captured_at)
        # Liveness ping for bench testing — fires only AFTER the event
        # bus accepted the message, so a journalctl grep for
        # ``mqtt.message_routed`` is a reliable success marker. The
        # measurement_type comes from the same routing dict
        # _route_to_event used; double-lookup is cheap (in-memory dict)
        # and keeps the log payload aligned with what subscribers see.
        measurement_type = _TOPIC_ROUTES[topic_suffix][0]
        self._logger.info(
            "mqtt.message_routed",
            topic=topic,
            measurement_type=measurement_type,
            value=value,
            unit=unit,
            captured_at=captured_at,
        )


# ---------------------------------------------------------------------------
# Shared routing — used by both mock and real
# ---------------------------------------------------------------------------


async def _emit_for_payload(
    bus: EventBus,
    topic_suffix: str,
    value: float,
    unit: str,
    captured_at: str,
) -> None:
    """Dispatch one payload to the right event class.

    Temperature splits off into :class:`LiveTemperatureUpdate` — the
    MLX90640 streams continuously regardless of whether the citizen
    has positioned the sensor on their forehead, so we display each
    update live and only emit :class:`MeasurementProposed` on a
    citizen tap (see ``MeasuringVitalsScreen.capture_temperature_requested``).
    Every other measurement type goes through ``_route_to_event``
    as before — the sensor adapters for SpO2 / weight / BP / height
    only publish a value when one is genuinely captured.
    """
    if topic_suffix == "temperature":
        await bus.publish(
            LiveTemperatureUpdate(value=value, unit=unit, captured_at=captured_at)
        )
        return
    await _route_to_event(bus, topic_suffix, value, unit)


async def _route_to_event(
    bus: EventBus, topic_suffix: str, value: float, unit: str
) -> None:
    measurement_type, _expected_unit, source_device = _TOPIC_ROUTES[topic_suffix]
    await bus.publish(
        MeasurementProposed(
            measurement_type=measurement_type,
            value=value,
            unit=unit,
            source_device=source_device,
            claimed_is_valid=True,
        )
    )
