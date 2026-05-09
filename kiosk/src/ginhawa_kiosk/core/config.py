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
    # Mosquitto auth. Username has a sensible default that matches the
    # ACL we ship (``ginhawa_kiosk`` — underscore, not hyphen; mosquitto
    # treats them as different identities). Password is REQUIRED — it
    # must come from kiosk.env at runtime, never a literal in source.
    # Production Mosquitto rejects anonymous connects (allow_anonymous
    # false), so a missing password should fail loud at boot rather
    # than silently degrade to an unauthenticated retry loop.
    MQTT_USERNAME: str = "ginhawa_kiosk"
    MQTT_PASSWORD: str

    # --- Thermal printer (ESC/POS over USB; portable across vendors) -----
    # VID/PID. Default is the Xprinter XP-58IIH (0x0416 / 0x5011); override
    # via ``KIOSK_PRINTER_VENDOR_ID`` / ``KIOSK_PRINTER_PRODUCT_ID`` to
    # match deployment hardware. Encoded as integers in env (decimal or
    # hex with ``0x`` prefix); pydantic accepts both forms. Verify per
    # unit with ``lsusb`` before deploying.
    #
    # The 2026-05-04 bench Pi runs an STM-based generic 58mm clone
    # (0x0483 / 0x070b); its commit a115be0 hard-coded those VIDs as
    # the default. Prompt 7.1 supersedes that workaround by making the
    # values env-var-configurable — set them in the bench Pi's
    # environment file rather than in the source. See
    # ``kiosk/docs/runbook.md`` "Printer hardware portability".
    KIOSK_PRINTER_VENDOR_ID: int = 0x0416
    KIOSK_PRINTER_PRODUCT_ID: int = 0x5011

    # USB endpoints for status reads / command writes.
    # ``None`` = python-escpos auto-detect (correct for most Xprinter and
    # Epson units). Override when auto-detect picks the wrong endpoint —
    # symptom is ``ValueError: Invalid endpoint address 0xNN`` mid-print.
    # STM-based generic 58mm clones typically expose IN at ``0x81`` and
    # OUT at ``0x01``; find your printer's actual values with
    # ``lsusb -v -d <vid>:<pid> | grep -A 2 bEndpointAddress``.
    KIOSK_PRINTER_USB_IN_ENDPOINT: int | None = None
    KIOSK_PRINTER_USB_OUT_ENDPOINT: int | None = None

    # Whether the printer responds to ESC/POS bidirectional status
    # queries (DLE EOT n, GS r n). Generic STM-based printers often
    # don't — paper_status() raises ValueError on those. When False, the
    # printer service skips paper-status checks and assumes paper is
    # present (best-effort printing). Default ``True`` matches Xprinter
    # and Epson behaviour.
    KIOSK_PRINTER_SUPPORTS_STATUS_QUERY: bool = True

    # python-escpos profile name (e.g., ``"TM-T88III"``). ``None`` =
    # library default. Some profiles trigger device queries on init that
    # break on non-spec-compliant hardware; setting ``None`` is the safe
    # fallback for unknown printers.
    KIOSK_PRINTER_PROFILE: str | None = None

    # --- Observability ---------------------------------------------------
    LOG_LEVEL: str = "INFO"

    # --- Mode switch (see module docstring) -----------------------------
    MOCK_HARDWARE: bool = False

    @field_validator(
        "KIOSK_DB_KEY",
        "KIOSK_API_KEY",
        "KIOSK_DEVICE_ID",
        "MQTT_PASSWORD",
    )
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
