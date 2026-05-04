"""Bench-test the production EscPosPrinterService end-to-end.

Replaces the earlier ``bench_printer_direct.py`` bypass that talked
straight to ``escpos.printer.Usb``. This script exercises the full
production code path (``EscPosPrinterService``, validation, the
language-of-session render, the cut at the end) so commissioning a
new printer model can be validated against the same code that runs
in the kiosk.

Run on the bench Pi with the printer connected and powered::

    KIOSK_PRINTER_VENDOR_ID=0x0483 \\
    KIOSK_PRINTER_PRODUCT_ID=0x070b \\
    KIOSK_PRINTER_USB_IN_ENDPOINT=0x81 \\
    KIOSK_PRINTER_USB_OUT_ENDPOINT=0x01 \\
    KIOSK_PRINTER_SUPPORTS_STATUS_QUERY=false \\
      uv run python -m ginhawa_kiosk.scripts.bench_printer_full

The script:

* prints two test receipts (one English, one Tagalog) through the
  production ``EscPosPrinterService`` — the same class the kiosk
  uses in the PRINTING state;
* reads the four portability settings from the environment so the
  same invocation that worked in the bench is what the kiosk will
  use in production;
* reports each ``PrintedStatus`` to stdout;
* exits with status 0 on two successes, 1 on any failure (so it
  can be wired into a commissioning checklist).

The script does NOT touch the kiosk database — the Citizen,
Session, and Measurement rows it uses are in-memory test fixtures
constructed at runtime. Receipts printed during commissioning are
NOT clinical records.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

import structlog

from ..db.models import Citizen, Measurement
from ..db.models import Session as SessionModel
from ..services.printer import (
    EscPosPrinterService,
    PrinterService,
    PrintResult,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bench_citizen() -> Citizen:
    return Citizen(
        id=str(uuid.uuid4()),
        rfid_uid="BENCH_PRINTER_TEST",
        full_name="Maria Bench Test",
        dob="1985-04-12",
        sex="F",
        barangay="Bench Lab",
        phone=None,
        consent_version="bench-v1",
        consent_given_at=_utc_now_iso(),
        registered_at=_utc_now_iso(),
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at=_utc_now_iso(),
    )


def _bench_session(citizen: Citizen) -> SessionModel:
    return SessionModel(
        id=str(uuid.uuid4()),
        citizen_id=citizen.id,
        device_id="bench-kiosk",
        started_at=_utc_now_iso(),
        ended_at=None,
        status="in_progress",
        error_reason=None,
        measurement_path="full",
        printed_status="not_requested",
        synced=0,
        updated_at=_utc_now_iso(),
    )


def _bench_measurements(session: SessionModel) -> list[Measurement]:
    now = _utc_now_iso()
    rows: list[tuple[str, float, str]] = [
        ("systolic_bp", 128.0, "mmHg"),
        ("diastolic_bp", 82.0, "mmHg"),
        ("heart_rate", 74.0, "bpm"),
        ("spo2", 98.0, "%"),
        ("temperature", 36.6, "C"),
        ("height", 158.0, "cm"),
        ("weight", 61.0, "kg"),
    ]
    return [
        Measurement(
            id=str(uuid.uuid4()),
            session_id=session.id,
            type=t,
            value=v,
            unit=u,
            source_device="bench",
            measured_at=now,
            is_valid=1,
            validation_notes=None,
            raw_json=None,
            synced=0,
            updated_at=now,
        )
        for t, v, u in rows
    ]


def _build_service_from_env() -> PrinterService:
    """Construct an EscPosPrinterService from KIOSK_PRINTER_* env vars.

    Reads the env directly rather than going through ``Settings`` so
    the bench script doesn't require a provisioned ``KIOSK_DB_KEY``
    or any of the other production secrets.
    """
    vendor = int(os.environ.get("KIOSK_PRINTER_VENDOR_ID", "0x0416"), 0)
    product = int(os.environ.get("KIOSK_PRINTER_PRODUCT_ID", "0x5011"), 0)

    in_ep_raw = os.environ.get("KIOSK_PRINTER_USB_IN_ENDPOINT")
    out_ep_raw = os.environ.get("KIOSK_PRINTER_USB_OUT_ENDPOINT")
    in_ep = int(in_ep_raw, 0) if in_ep_raw else None
    out_ep = int(out_ep_raw, 0) if out_ep_raw else None

    supports_status = (
        os.environ.get("KIOSK_PRINTER_SUPPORTS_STATUS_QUERY", "true").lower() != "false"
    )
    profile = os.environ.get("KIOSK_PRINTER_PROFILE") or None

    print(
        "  service config: "
        f"vid={hex(vendor)} pid={hex(product)} "
        f"in_ep={hex(in_ep) if in_ep else 'auto'} "
        f"out_ep={hex(out_ep) if out_ep else 'auto'} "
        f"supports_status_query={supports_status} "
        f"profile={profile or 'default'}"
    )

    return EscPosPrinterService(
        vendor_id=vendor,
        product_id=product,
        in_endpoint=in_ep,
        out_endpoint=out_ep,
        supports_status_query=supports_status,
        profile=profile,
    )


async def _run_one(
    service: PrinterService,
    language: str,
    label: str,
) -> PrintResult:
    citizen = _bench_citizen()
    session = _bench_session(citizen)
    measurements = _bench_measurements(session)
    print(f"\n[{label}] printing {language!r} test receipt...")
    result = await service.print_session_report(
        session,
        citizen,
        measurements,
        language=language,  # type: ignore[arg-type]
    )
    print(f"[{label}] result: success={result.success} status={result.printed_status}")
    return result


async def _run() -> int:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    print("== ginhawa-kiosk bench_printer_full ==")
    print("Exercising the production EscPosPrinterService end-to-end.")
    print("Two receipts will print: one English, one Tagalog.\n")

    service = _build_service_from_env()

    # Pre-flight checks — surface common failures before kicking off
    # the actual print.
    print("\n  pre-flight: is_available()...")
    if not service.is_available():
        print(
            "  FAIL: printer is_available() returned False — check VID:PID, "
            "udev permissions, and that CUPS isn't holding the device."
        )
        return 1
    print("  ok: device reachable")

    print("  pre-flight: is_paper_present()...")
    paper = service.is_paper_present()
    print(f"  result: {paper} (note: returns True if status_query is disabled)")

    en_result = await _run_one(service, "en", "EN")
    tl_result = await _run_one(service, "tl", "TL")

    print("\n== summary ==")
    print(f"  English receipt: {en_result}")
    print(f"  Tagalog receipt: {tl_result}")
    if en_result.success and tl_result.success:
        print("\nAll receipts printed successfully.")
        return 0
    print("\nOne or more receipts failed — check printer state and env vars.")
    return 1


def main() -> int:  # pragma: no cover - CLI surface, exercised on Pi
    return asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
