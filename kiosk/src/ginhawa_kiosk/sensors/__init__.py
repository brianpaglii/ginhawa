"""Sensor adapter registry.

Four sensor types, each with a mock and a real implementation:

* RFID reader (MFRC522 over SPI on the Pi)
* Xiaomi Smart Scale S200 (BLE, via xiaomi-ble — see ADR-0017)
* Omron HEM-7155T BP cuff (BLE Blood Pressure Service 0x1810)
* MQTT subscriber for ESP32-A (SpO2, heart rate) and ESP32-B
  (temperature, height)

The factory in this module picks mock or real per sensor based on
``Settings.MOCK_HARDWARE``. Per CLAUDE.md, that flag is the SINGLE
switch between dev and prod and is read here and only here.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.config import Settings
from ..fsm.event_bus import EventBus
from .base import Sensor, SensorUnavailable
from .ble_lock import BleAdapterLock
from .mqtt_sensors import MockMqttSensors, MqttSensorSubscriber
from .omron_bp import MockOmronBp, OmronBpSensor
from .rfid import Mfrc522RfidReader, MockRfidReader
from .xiaomi_scale import MockXiaomiScale, XiaomiScaleSensor


__all__ = [
    "BleAdapterLock",
    "Mfrc522RfidReader",
    "MockMqttSensors",
    "MockOmronBp",
    "MockRfidReader",
    "MockXiaomiScale",
    "MqttSensorSubscriber",
    "OmronBpSensor",
    "Sensor",
    "SensorUnavailable",
    "XiaomiScaleSensor",
    "create_all_sensors",
    "create_mqtt_sensors",
    "create_omron_bp",
    "create_rfid_reader",
    "create_xiaomi_scale",
]


def create_rfid_reader(bus: EventBus, settings: Settings) -> Sensor:
    """Mock when MOCK_HARDWARE is true; MFRC522 hardware otherwise."""
    if settings.MOCK_HARDWARE:
        return MockRfidReader(bus)
    return Mfrc522RfidReader(bus)  # pragma: no cover - Pi-only path


def create_xiaomi_scale(
    bus: EventBus,
    settings: Settings,
    db: Session,
    *,
    ble_lock: BleAdapterLock | None = None,
) -> Sensor:
    if settings.MOCK_HARDWARE:
        return MockXiaomiScale(bus)
    return XiaomiScaleSensor(  # pragma: no cover - hardware path
        bus, db, ble_lock=ble_lock
    )


def create_omron_bp(
    bus: EventBus,
    settings: Settings,
    db: Session,
    *,
    ble_lock: BleAdapterLock | None = None,
) -> Sensor:
    if settings.MOCK_HARDWARE:
        return MockOmronBp(bus)
    return OmronBpSensor(bus, db, ble_lock=ble_lock)  # pragma: no cover


def create_mqtt_sensors(bus: EventBus, settings: Settings, db: Session) -> Sensor:
    if settings.MOCK_HARDWARE:
        return MockMqttSensors(bus)
    return MqttSensorSubscriber(  # pragma: no cover - real-network path
        bus,
        db,
        broker_host=settings.MQTT_BROKER_HOST,
        broker_port=settings.MQTT_BROKER_PORT,
        username=settings.MQTT_USERNAME,
        password=settings.MQTT_PASSWORD,
    )


def create_all_sensors(
    bus: EventBus, settings: Settings, db: Session
) -> dict[str, Sensor]:
    """Build the kiosk's full sensor set keyed by name.

    The application's lifecycle manager calls ``start()`` and
    ``stop()`` on each Sensor in coordination with the FSM and sync
    daemon.

    A single :class:`BleAdapterLock` is constructed here and shared
    between the Xiaomi scale (which holds the adapter via continuous
    passive scan) and the Omron BP cuff (which needs exclusive
    access for directed connects). CLAUDE.md "no concurrent BLE";
    see :mod:`ginhawa_kiosk.sensors.ble_lock`.
    """
    ble_lock = BleAdapterLock()
    return {
        "rfid": create_rfid_reader(bus, settings),
        "xiaomi_scale": create_xiaomi_scale(bus, settings, db, ble_lock=ble_lock),
        "omron_bp": create_omron_bp(bus, settings, db, ble_lock=ble_lock),
        "mqtt_sensors": create_mqtt_sensors(bus, settings, db),
    }
