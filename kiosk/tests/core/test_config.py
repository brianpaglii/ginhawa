"""Settings tests focused on the MQTT auth fields.

Adjacent to ``test_smoke.test_settings_reject_missing_required``: the
broader smoke test asserts the existing required-secret triad blocks
boot, this module pins the MQTT-specific behaviour added when
Mosquitto auth was wired up.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ginhawa_kiosk.core.config import Settings, get_settings


# Other required fields the validator needs satisfied so the test can
# isolate MQTT_PASSWORD as the missing one.
_OTHER_REQUIRED_ENV: dict[str, str] = {
    "KIOSK_DB_KEY": "config-test-db-key",  # pragma: allowlist secret
    "KIOSK_API_KEY": "config-test-api-key",  # pragma: allowlist secret
    "KIOSK_DEVICE_ID": "00000000-0000-0000-0000-000000000401",
}


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    # Settings is memoised; tests that monkeypatch env need a clean
    # cache so their env mutations actually take effect.
    get_settings.cache_clear()


# Verifies MQTT_PASSWORD is treated as a required secret with no
# in-source default. Mortality: would fail if a future refactor
# accidentally restored a literal default — Mosquitto would then
# silently boot with the wrong password and the kiosk would loop on
# auth failures.
def test_mqtt_password_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _OTHER_REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MQTT_PASSWORD", raising=False)
    monkeypatch.delenv("MOCK_HARDWARE", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        # _env_file=None disables .env discovery so a stray repo-root
        # .env doesn't satisfy MQTT_PASSWORD and mask the failure.
        Settings(_env_file=None)  # type: ignore[call-arg]

    # Be specific about WHICH field tripped the validator — a generic
    # ValidationError could come from any required field.
    assert "MQTT_PASSWORD" in str(exc_info.value)


# Verifies MQTT_PASSWORD's empty-string rejector. Mortality: would
# fail if MQTT_PASSWORD were dropped from the field_validator block,
# letting an empty env var slip through (the broker would still
# reject the connect, but the kiosk should refuse to boot rather
# than loop on a known-bad credential).
def test_mqtt_password_rejects_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _OTHER_REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MQTT_PASSWORD", "   ")  # whitespace only

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


# Verifies MQTT_USERNAME defaults to ``ginhawa_kiosk`` (underscore).
# Mortality: would fail if someone restored the prior hyphenated
# default — Mosquitto's password file uses the underscore form, so
# the hyphen variant is a silent auth-fail hazard.
def test_mqtt_username_default_is_underscore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _OTHER_REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(
        "MQTT_PASSWORD",
        "config-test-mqtt-pass",  # pragma: allowlist secret
    )
    # Make sure the host env doesn't already define MQTT_USERNAME, or
    # the test would assert against an override rather than the default.
    monkeypatch.delenv("MQTT_USERNAME", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.MQTT_USERNAME == "ginhawa_kiosk"
