"""Kiosk runtime configuration.

Settings are loaded from environment variables with `.env` fallback,
mirroring the cloud's pattern. The single most important field is
``MOCK_HARDWARE`` — a boolean switch that selects between mock and
production sensor implementations across the whole package.

================================================================
THE MOCK_HARDWARE FLAG IS THE CENTRAL DESIGN DECISION OF PHASE 2.
================================================================

When ``MOCK_HARDWARE=true``:
* All BLE, MQTT, RFID, and printer integrations resolve to the
  mock implementations under ``sensors/`` and ``services/``.
* No physical hardware is required.
* Tests, laptop development, and CI all run in this mode.

When ``MOCK_HARDWARE=false`` (production on the Pi):
* BLE goes to bleak / omblepy / Xiaomi BLE.
* MQTT goes to a real Mosquitto broker on localhost.
* RFID goes to the MFRC522 over SPI.
* Printer goes to python-escpos over USB.

This is the *only* runtime switch between development and production
behaviour for the kiosk. Subpackages MUST consult ``Settings.MOCK_HARDWARE``
through the ``get_settings()`` accessor — never sniff env vars
directly, never branch on ``platform.machine() == 'aarch64'``, never
introspect ``/sys``. One switch, one truth, one place.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Local storage ---------------------------------------------------
    # Path to the SQLCipher-encrypted SQLite file. Default is under the
    # invoking user's home so the script can run without root on the Pi.
    KIOSK_DB_PATH: Path = Field(default=Path.home() / ".ginhawa" / "kiosk.db")

    # The SQLCipher AES-256 passphrase. Required — there is no default.
    # In production this is derived at install time from the Pi's machine-id
    # plus an installation-time salt and written to a root-only file; the
    # systemd unit reads it from there. Never log this value.
    KIOSK_DB_KEY: str

    # --- Cloud sync ------------------------------------------------------
    CLOUD_API_URL: str = "https://cloud.ginhawa.local"
    KIOSK_API_KEY: str  # device API key issued by the cloud admin
    KIOSK_DEVICE_ID: str  # UUID matching device_credentials.device_id

    # --- Local broker ----------------------------------------------------
    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 1883

    # --- Thermal printer (Xprinter XP-58IIH, ESC/POS over USB) ----------
    # VID/PID typically 0x0416 / 0x5011 — verify per unit with `lsusb`
    # before deploying. Encoded as integers in env (decimal or hex with
    # ``0x`` prefix); pydantic accepts both forms.
    PRINTER_VENDOR_ID: int = 0x0416
    PRINTER_PRODUCT_ID: int = 0x5011

    # --- Observability ---------------------------------------------------
    LOG_LEVEL: str = "INFO"

    # --- Mode switch (see module docstring) -----------------------------
    MOCK_HARDWARE: bool = False

    @field_validator("KIOSK_DB_KEY", "KIOSK_API_KEY", "KIOSK_DEVICE_ID")
    @classmethod
    def _reject_empty_secret(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty or whitespace")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor. Tests that need a fresh Settings call
    ``get_settings.cache_clear()`` first."""
    # pydantic-settings populates required fields from the env / .env at
    # construction time; mypy doesn't model that and flags the missing
    # kwargs. Suppressing here is preferable to spreading the ignore
    # across every call site.
    return Settings()  # type: ignore[call-arg]
