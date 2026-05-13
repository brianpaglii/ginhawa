"""MAC-address filter on the Xiaomi scale's advert handler.

The ``xiaomi_ble`` library's ``XiaomiBluetoothDeviceData.update()`` is
stateful: calling it with any device's advert returns the cached mass
from the last scale advert it parsed. Without filtering at the receipt
boundary, every nearby BLE device's advert routes through the gate
carrying session-1's stored weight, and the K=3 stability check fires
on identical re-broadcasts. See
``docs/audits/2026-05-13-scale-stale-readings-audit.md``; the
follow-up 2026-05-13 bench confirmed that the actual mechanism was
library state pollution rather than scale-side rebroadcast.

These tests pin the MAC filter at the very top of
``_on_advertisement``: non-matching MACs never reach the xiaomi_ble
library, matching MACs do.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ginhawa_kiosk.fsm import EventBus
from ginhawa_kiosk.sensors.xiaomi_scale import XiaomiScaleSensor


_SCALE_MAC = "D0:7B:6F:88:FD:93"
_OTHER_MAC = "00:5F:BF:C2:F8:01"


def _make_ble_device(address: str) -> BLEDevice:
    # ``rssi`` is deprecated on bleak ≥ 0.21 BLEDevice; omit it so the
    # test suite doesn't emit a DeprecationWarning per construction.
    return BLEDevice(address=address, name=None, details={})


def _make_advert() -> AdvertisementData:
    return AdvertisementData(
        local_name=None,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        tx_power=None,
        rssi=-50,
        platform_data=(),
    )


def _make_sensor(scale_mac: str, mocker: Any) -> tuple[XiaomiScaleSensor, Any]:
    """Sensor with a stubbed xiaomi_ble device-data layer so the
    filter behaviour can be observed without touching real BLE.

    Returns ``(sensor, library_mock)`` so tests can drive
    ``_on_advertisement`` on the sensor and assert call counts on
    the library mock without having to None-check
    ``sensor._device_data`` (typed as ``Any | None``).
    """
    sensor = XiaomiScaleSensor(
        EventBus(),
        mocker.MagicMock(name="DbSession"),
        scale_mac=scale_mac,
    )
    # Stub the library. The mock's update() returns an object whose
    # entity_values is an empty dict — the post-filter path then
    # short-circuits in extract_mass_kg (no 'mass' key) and the gate
    # is never called. We only assert on whether update() ran at all.
    fake_update_result = mocker.MagicMock()
    fake_update_result.entity_values = {}
    library_mock = mocker.MagicMock()
    library_mock.update.return_value = fake_update_result
    sensor._device_data = library_mock
    return sensor, library_mock


# A foreign device's advert (e.g., a nearby Omron BP cuff) must NOT
# reach the xiaomi_ble library. The library is stateful and its
# update() returns cached mass from the last scale advert it parsed
# regardless of which device emitted the new advert. Dropping at the
# receipt boundary is the only way to stop the cached value from
# rebroadcasting through unrelated MACs.
# Mortality: would fail if the filter were removed or applied AFTER
# the library call.
@pytest.mark.asyncio
async def test_non_matching_mac_advert_dropped(mocker: Any) -> None:
    sensor, library_mock = _make_sensor(_SCALE_MAC, mocker)
    await sensor._on_advertisement(_make_ble_device(_OTHER_MAC), _make_advert())
    assert library_mock.update.call_count == 0


# The scale's own advert must reach the library. The reverse of the
# previous test pins that the filter doesn't accidentally drop the
# only MAC it's supposed to admit.
# Mortality: would fail if the filter were inverted or the
# comparison case-folded incorrectly.
@pytest.mark.asyncio
async def test_matching_mac_advert_processed(mocker: Any) -> None:
    sensor, library_mock = _make_sensor(_SCALE_MAC, mocker)
    # Lowercase input on purpose — bleak typically emits uppercase,
    # but we should not depend on that.
    await sensor._on_advertisement(_make_ble_device(_SCALE_MAC.lower()), _make_advert())
    assert library_mock.update.call_count == 1


# Case-insensitive match works in the other direction too: a sensor
# configured with a lowercase MAC must still admit uppercase MACs
# from bleak. The constructor uppercases the stored value; the
# comparison uppercases the runtime advert.
# Mortality: would fail if only one side were normalised.
@pytest.mark.asyncio
async def test_case_insensitive_mac_match(mocker: Any) -> None:
    sensor, library_mock = _make_sensor(_SCALE_MAC.lower(), mocker)
    await sensor._on_advertisement(_make_ble_device(_SCALE_MAC), _make_advert())
    assert library_mock.update.call_count == 1


# Operators who paste the MAC out of bluetoothctl sometimes get
# leading or trailing whitespace. The constructor strips. Pinned so
# a future "tighten validation" change doesn't silently break
# deployments whose env file has a trailing space.
# Mortality: would fail if the strip were removed.
@pytest.mark.asyncio
async def test_mac_stripped_of_whitespace(mocker: Any) -> None:
    sensor, library_mock = _make_sensor(f"  {_SCALE_MAC}  ", mocker)
    await sensor._on_advertisement(_make_ble_device(_SCALE_MAC), _make_advert())
    assert library_mock.update.call_count == 1


# Empty MAC = legacy "accept everything" path. Used in dev / mock
# environments where the operator hasn't run commissioning yet. A
# one-shot warning logs at the first advert so the missing setting
# is loud in journalctl; subsequent adverts don't re-warn (otherwise
# the journal floods at ~10/s).
# Mortality: would fail if the warning fired more than once OR if
# the empty-MAC path silently dropped adverts.
@pytest.mark.asyncio
async def test_no_mac_configured_accepts_all_with_warning(mocker: Any) -> None:
    sensor, library_mock = _make_sensor("", mocker)
    with structlog.testing.capture_logs() as logs:
        await sensor._on_advertisement(_make_ble_device(_OTHER_MAC), _make_advert())
        await sensor._on_advertisement(_make_ble_device(_SCALE_MAC), _make_advert())

    # Both adverts reached the library.
    assert library_mock.update.call_count == 2
    # Exactly one warning, despite two adverts.
    warnings = [
        e
        for e in logs
        if e.get("event") == "xiaomi_scale.mac_filter_disabled"
        and e.get("log_level") == "warning"
    ]
    assert len(warnings) == 1
