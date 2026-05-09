"""Wiring smoke test — proof that the kiosk package can be imported,
that ``Settings`` accepts all required env vars, and that the
package-level public surface (``configure_logging``, ``bind_session_id``,
``record_audit``, ``build_sensor_set``) is reachable.

This is the kiosk equivalent of the cloud's ``/health`` test — it does
not exercise behaviour, only proves the package wires up.
"""

from __future__ import annotations

import pytest

import ginhawa_kiosk


_REQUIRED_ENV: dict[str, str] = {
    "KIOSK_DB_KEY": "smoke-test-db-key",  # pragma: allowlist secret
    "KIOSK_API_KEY": "smoke-test-api-key",  # pragma: allowlist secret
    "KIOSK_DEVICE_ID": "00000000-0000-0000-0000-000000000401",
    "MQTT_PASSWORD": "smoke-test-mqtt-pass",  # pragma: allowlist secret
}


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Settings is memoised by ``functools.lru_cache``; clear before
    each test so monkeypatched env vars take effect."""
    from ginhawa_kiosk.core.config import get_settings

    get_settings.cache_clear()


def test_package_exposes_version() -> None:
    assert hasattr(ginhawa_kiosk, "__version__")
    assert isinstance(ginhawa_kiosk.__version__, str)


def test_settings_load_with_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MOCK_HARDWARE", "true")

    from ginhawa_kiosk.core.config import get_settings

    settings = get_settings()
    assert settings.KIOSK_API_KEY == "smoke-test-api-key"  # pragma: allowlist secret
    assert settings.KIOSK_DEVICE_ID == "00000000-0000-0000-0000-000000000401"
    assert settings.MOCK_HARDWARE is True
    assert settings.MQTT_BROKER_PORT == 1883  # default applied


def test_settings_reject_missing_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Settings constructed with no env vars set MUST fail loud — the
    point of the required fields is to refuse to start with no key."""
    for key in _REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MOCK_HARDWARE", raising=False)

    from pydantic import ValidationError

    from ginhawa_kiosk.core.config import Settings

    # Disable .env discovery so a stray repo-root .env doesn't satisfy
    # the required fields and mask the failure.
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_logging_configures_without_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MOCK_HARDWARE", "true")

    from ginhawa_kiosk.core.config import get_settings
    from ginhawa_kiosk.core.logging import (
        bind_session_id,
        configure_logging,
        get_logger,
    )

    configure_logging(get_settings())
    logger = get_logger("smoke")
    logger.info("smoke.boot")
    with bind_session_id("smoke-session-id"):
        logger.info("smoke.during_session")


def test_mock_sensor_set_builds_in_mock_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MOCK_HARDWARE", "true")

    from ginhawa_kiosk.core.config import get_settings
    from ginhawa_kiosk.fsm import EventBus
    from ginhawa_kiosk.sensors import (
        MockMqttSensors,
        MockOmronBp,
        MockRfidReader,
        MockXiaomiScale,
        create_rfid_reader,
        create_xiaomi_scale,
        create_omron_bp,
        create_mqtt_sensors,
    )

    settings = get_settings()
    bus = EventBus()
    # db is only consulted on .start() for the real implementations;
    # the mocks ignore it. Pass None to confirm that.
    assert isinstance(create_rfid_reader(bus, settings), MockRfidReader)
    assert isinstance(
        create_xiaomi_scale(bus, settings, db=None),  # type: ignore[arg-type]
        MockXiaomiScale,
    )
    assert isinstance(
        create_omron_bp(bus, settings, db=None),  # type: ignore[arg-type]
        MockOmronBp,
    )
    assert isinstance(
        create_mqtt_sensors(bus, settings, db=None),  # type: ignore[arg-type]
        MockMqttSensors,
    )
