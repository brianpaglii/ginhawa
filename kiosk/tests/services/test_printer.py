"""Printer service contract.

Covers the language-of-session receipt format, the PrintedStatus
contract, and the factory's MOCK_HARDWARE switch. Real-USB
python-escpos behaviour on the Pi is exercised end-to-end during
the bench-test verification runs (see ``kiosk/docs/verification/``);
the unit tests below stay hardware-free.
"""

from __future__ import annotations

import uuid

import pytest

from ginhawa_kiosk.db.models import Citizen, Measurement
from ginhawa_kiosk.db.models import Session as SessionModel
from ginhawa_kiosk.services.printer import (
    MockPrinterService,
    PrintedStatus,
    PrintResult,
    XprinterPrinterService,
    create_printer_service,
)


def _make_citizen() -> Citizen:
    return Citizen(
        id=str(uuid.uuid4()),
        rfid_uid="A3F2C901",
        full_name="Maria Dela Cruz",
        dob="1984-05-03",
        sex="F",
        barangay="San Roque",
        phone=None,
        consent_version="v1",
        consent_given_at="2026-05-03T10:00:00+00:00",
        registered_at="2026-05-03T10:00:00+00:00",
        registered_by=None,
        is_active=1,
        synced=0,
        updated_at="2026-05-03T10:00:00+00:00",
    )


def _make_session(citizen: Citizen) -> SessionModel:
    return SessionModel(
        id=str(uuid.uuid4()),
        citizen_id=citizen.id,
        device_id="kiosk-test",
        started_at="2026-05-03T14:23:00+00:00",
        ended_at=None,
        status="in_progress",
        error_reason=None,
        measurement_path="full",
        printed_status="not_requested",
        synced=0,
        updated_at="2026-05-03T14:23:00+00:00",
    )


def _meas(
    session: SessionModel,
    *,
    type: str,  # noqa: A002 — mirrors the schema column name
    value: float,
    unit: str,
    is_valid: int = 1,
) -> Measurement:
    return Measurement(
        id=str(uuid.uuid4()),
        session_id=session.id,
        type=type,
        value=value,
        unit=unit,
        source_device="test",
        measured_at="2026-05-03T14:23:30+00:00",
        is_valid=is_valid,
        validation_notes=None,
        raw_json=None,
        synced=0,
        updated_at="2026-05-03T14:23:30+00:00",
    )


# ---------------------------------------------------------------------
# 1. English-language receipt: English labels, English closing
# ---------------------------------------------------------------------
# Verifies an English-session receipt renders entirely in English:
# the demographics labels (Date/Name/Age/Sex/Barangay), the
# MEASUREMENTS section heading, the English measurement labels
# (Systolic BP, Heart Rate, ...), and the English closing ("Thank
# you for your visit."). The Tagalog forms (Petsa / Pangalan / Edad
# / Salamat) MUST NOT appear — CLAUDE.md says receipts print in the
# language selected at session start, never mixed.
# Mortality: would fail if any English label were renamed, the
# English closing line dropped, OR if a Tagalog label leaked into
# the English-session output (silent regression to the old mixed-
# bilingual format).
@pytest.mark.asyncio
async def test_print_format_english() -> None:
    p = MockPrinterService()
    citizen = _make_citizen()
    session = _make_session(citizen)
    measurements = [
        _meas(session, type="systolic_bp", value=128.0, unit="mmHg"),
        _meas(session, type="diastolic_bp", value=82.0, unit="mmHg"),
        _meas(session, type="heart_rate", value=74.0, unit="bpm"),
        _meas(session, type="spo2", value=98.0, unit="%"),
        _meas(session, type="temperature", value=36.6, unit="C"),
    ]

    result = await p.print_session_report(session, citizen, measurements, language="en")

    assert result == PrintResult(True, PrintedStatus.PRINTED_OK)
    receipt = p.mock_print_history[0]

    # English demographics labels
    assert "Date: 2026-05-03 14:23" in receipt
    assert "Name: Maria Dela Cruz" in receipt
    assert "Age: 41" in receipt or "Age: 42" in receipt
    assert "Sex: F" in receipt
    assert "Barangay: San Roque" in receipt
    # English section heading + measurement labels
    assert "MEASUREMENTS" in receipt
    assert "Systolic BP" in receipt and "128 mmHg" in receipt
    assert "Diastolic BP" in receipt and "82 mmHg" in receipt
    assert "Heart Rate" in receipt and "74 bpm" in receipt
    assert "SpO2" in receipt and "98 %" in receipt
    assert "Temperature" in receipt and "36.6 C" in receipt
    # English closing + non-diagnostic footer
    assert "Thank you for your visit." in receipt
    assert "(Not a medical diagnosis" in receipt

    # No Tagalog leakage
    assert "Petsa" not in receipt
    assert "Pangalan" not in receipt
    assert "Edad" not in receipt
    assert "Kasarian" not in receipt
    assert "Salamat" not in receipt
    assert "Hindi medikal" not in receipt
    assert "MGA SUKAT" not in receipt

    # Section ordering: header → demographics → measurements → closing.
    header_pos = receipt.index("GINHAWA HEALTH MONITORING KIOSK")
    name_pos = receipt.index("Name: Maria Dela Cruz")
    measurements_pos = receipt.index("MEASUREMENTS")
    closing_pos = receipt.index("Thank you for your visit.")
    assert header_pos < name_pos < measurements_pos < closing_pos


# ---------------------------------------------------------------------
# 2. Tagalog-language receipt: Tagalog labels, Tagalog closing
# ---------------------------------------------------------------------
# Mirror of test 1 for ``language='tl'``. The receipt must render in
# Tagalog: Petsa / Pangalan / Edad / Kasarian / Barangay; "MGA SUKAT"
# section heading; Tagalog measurement labels (Sistoliko / Diastoliko
# / Tibok ng Puso / Temperatura / Taas / Timbang); Tagalog closing
# ("Salamat sa pagpapatingin! Maraming salamat po."). The English
# forms MUST NOT appear.
# Mortality: would fail if a Tagalog label drifted, the Tagalog
# closing dropped, OR if English text leaked into a TL receipt
# (which would re-introduce the mixed-bilingual format CLAUDE.md
# rules out).
@pytest.mark.asyncio
async def test_print_format_tagalog() -> None:
    p = MockPrinterService()
    citizen = _make_citizen()
    session = _make_session(citizen)
    measurements = [
        _meas(session, type="systolic_bp", value=128.0, unit="mmHg"),
        _meas(session, type="diastolic_bp", value=82.0, unit="mmHg"),
        _meas(session, type="heart_rate", value=74.0, unit="bpm"),
        _meas(session, type="temperature", value=36.6, unit="C"),
        _meas(session, type="height", value=158.0, unit="cm"),
        _meas(session, type="weight", value=61.0, unit="kg"),
    ]

    result = await p.print_session_report(session, citizen, measurements, language="tl")

    assert result == PrintResult(True, PrintedStatus.PRINTED_OK)
    receipt = p.mock_print_history[0]

    # Tagalog demographics labels
    assert "Petsa: 2026-05-03 14:23" in receipt
    assert "Pangalan: Maria Dela Cruz" in receipt
    assert "Edad:" in receipt
    assert "Kasarian: F" in receipt
    assert "Barangay: San Roque" in receipt
    # Tagalog section heading + measurement labels
    assert "MGA SUKAT" in receipt
    assert "Sistoliko" in receipt and "128 mmHg" in receipt
    assert "Diastoliko" in receipt and "82 mmHg" in receipt
    assert "Tibok ng Puso" in receipt and "74 bpm" in receipt
    assert "Temperatura" in receipt and "36.6 C" in receipt
    assert "Taas" in receipt and "158.0 cm" in receipt
    assert "Timbang" in receipt and "61.0 kg" in receipt
    # Derived BMI uses the Tagalog category label
    assert "BMI" in receipt
    # Tagalog closing + non-diagnostic footer
    assert "Salamat sa pagpapatingin!" in receipt
    assert "Maraming salamat po." in receipt
    assert "(Hindi medikal na diagnosis" in receipt

    # No English leakage
    assert "Date:" not in receipt
    assert "Name:" not in receipt
    assert "Age:" not in receipt
    assert "Sex:" not in receipt
    assert "MEASUREMENTS" not in receipt
    assert "Systolic BP" not in receipt
    assert "Heart Rate" not in receipt
    assert "Thank you for your visit." not in receipt
    assert "(Not a medical diagnosis" not in receipt


# ---------------------------------------------------------------------
# 3. Pre-print paper-out short-circuits the job
# ---------------------------------------------------------------------
# Verifies that when paper is not present at the start of the job,
# print_session_report returns PAPER_OUT_PRE without writing anything
# to mock_print_history (i.e., the job was abandoned before any
# rendering happened). Language is irrelevant on this path — the
# pre-flight check fires before the renderer runs — but we still
# pass one to keep the signature exercised.
# Mortality: would fail if the pre-flight paper check were skipped,
# or if the implementation rendered into history before checking
# paper status (which would falsely look like a partial print on the
# real device when the head was actually empty).
@pytest.mark.asyncio
async def test_print_session_report_paper_out_pre_short_circuits() -> None:
    p = MockPrinterService()
    p.paper_present = False
    citizen = _make_citizen()
    session = _make_session(citizen)

    result = await p.print_session_report(
        session,
        citizen,
        [_meas(session, type="weight", value=61.0, unit="kg")],
        language="en",
    )

    assert result == PrintResult(False, PrintedStatus.PAPER_OUT_PRE)
    assert p.mock_print_history == []
    assert p.is_paper_present() is False


# ---------------------------------------------------------------------
# 4. is_valid=0 / out-of-range readings are excluded
# ---------------------------------------------------------------------
# Out-of-range readings stay in the DB with is_valid=0 for diagnostic
# review (sensor calibration drift), but the citizen-facing receipt
# excludes them — printing a flagged value next to in-range ones
# would imply clinical equivalence we don't intend.
# Mortality: would fail if the printer started including is_valid=0
# rows, or if the filter were inverted (excluding valid ones), or if
# range-side filtering were removed and only unit-side filtering
# remained.
@pytest.mark.asyncio
async def test_print_excludes_invalid_measurements() -> None:
    p = MockPrinterService()
    citizen = _make_citizen()
    session = _make_session(citizen)
    measurements = [
        _meas(session, type="systolic_bp", value=128.0, unit="mmHg"),
        # Flagged out-of-range reading — must NOT appear on receipt.
        _meas(
            session,
            type="systolic_bp",
            value=320.0,
            unit="mmHg",
            is_valid=0,
        ),
        _meas(session, type="weight", value=61.0, unit="kg"),
    ]

    await p.print_session_report(session, citizen, measurements, language="en")

    receipt = p.mock_print_history[0]
    assert "128 mmHg" in receipt
    assert "320" not in receipt


# ---------------------------------------------------------------------
# 5. BMI is conditional on BOTH height and weight being present
# ---------------------------------------------------------------------
# The receipt computes and prints BMI (with the WHO category) when
# both height and weight are captured; if either is missing, BMI is
# omitted entirely — printing a BMI for a missing-height session
# would implicitly assume an average, which is wrong.
# Mortality: would fail if the BMI rendering ran on weight-only or
# height-only sessions, or if the WHO category label were dropped,
# or if the derivation used the wrong formula (kg / m² is the
# canonical form; kg / cm² silently produces a 100× smaller value).
@pytest.mark.asyncio
async def test_bmi_only_when_height_and_weight_present() -> None:
    p = MockPrinterService()
    citizen = _make_citizen()
    session = _make_session(citizen)

    # Case A: weight only — no BMI.
    await p.print_session_report(
        session,
        citizen,
        [_meas(session, type="weight", value=61.0, unit="kg")],
        language="en",
    )
    assert "BMI" not in p.mock_print_history[-1]

    # Case B: height only — no BMI.
    await p.print_session_report(
        session,
        citizen,
        [_meas(session, type="height", value=158.0, unit="cm")],
        language="en",
    )
    assert "BMI" not in p.mock_print_history[-1]

    # Case C: both present, normal BMI. 61.0 / 1.58² ≈ 24.4 → "Normal"
    # band (18.5–25.0).
    await p.print_session_report(
        session,
        citizen,
        [
            _meas(session, type="height", value=158.0, unit="cm"),
            _meas(session, type="weight", value=61.0, unit="kg"),
        ],
        language="en",
    )
    receipt = p.mock_print_history[-1]
    assert "BMI" in receipt
    assert "24.4" in receipt
    assert "Normal" in receipt

    # Cases D-F: WHO category boundaries. Each case pins a different
    # branch of _bmi_category — silent drift in the cut-offs would
    # mis-categorise readings on the receipt.
    cases = [
        # (height_cm, weight_kg, expected_category)
        (170.0, 50.0, "Underweight"),  # 50/1.7² ≈ 17.3
        (170.0, 80.0, "Overweight"),  # 80/1.7² ≈ 27.7
        (170.0, 95.0, "Obese"),  # 95/1.7² ≈ 32.9
    ]
    for h, w, expected in cases:
        await p.print_session_report(
            session,
            citizen,
            [
                _meas(session, type="height", value=h, unit="cm"),
                _meas(session, type="weight", value=w, unit="kg"),
            ],
            language="en",
        )
        assert expected in p.mock_print_history[-1]


# ---------------------------------------------------------------------
# 6. No-valid-measurements notice is language-specific
# ---------------------------------------------------------------------
# A session can complete with zero valid measurements (every reading
# was flagged is_valid=0, or the citizen aborted measurement). The
# receipt must still print — the citizen still walked up to the
# kiosk — with an explicit notice in the chosen language, including
# a "consult the BHW" pointer so the citizen knows to follow up.
# Mortality: would fail if the no-measurements branch raised, or
# silently rendered an empty section, or if the notice were the
# wrong language for the session, or if the BHW-consultation
# pointer were dropped (citizens deserve a follow-up path when the
# kiosk produced nothing usable).
@pytest.mark.asyncio
async def test_no_valid_measurements_prints_notice() -> None:
    p = MockPrinterService()
    citizen = _make_citizen()
    session = _make_session(citizen)
    measurements = [
        _meas(
            session,
            type="systolic_bp",
            value=400.0,
            unit="mmHg",
            is_valid=0,
        ),
    ]

    en = await p.print_session_report(session, citizen, measurements, language="en")
    assert en == PrintResult(True, PrintedStatus.PRINTED_OK)
    en_receipt = p.mock_print_history[-1]
    assert "No measurements captured. Please consult the BHW." in en_receipt
    assert "Walang nakuhang sukat." not in en_receipt
    assert "400" not in en_receipt

    tl = await p.print_session_report(session, citizen, measurements, language="tl")
    assert tl == PrintResult(True, PrintedStatus.PRINTED_OK)
    tl_receipt = p.mock_print_history[-1]
    assert "Walang nakuhang sukat. Sumangguni sa BHW." in tl_receipt
    assert "No measurements captured" not in tl_receipt


# ---------------------------------------------------------------------
# 7. Missing-device / mid-print failure are surfaced gracefully
# ---------------------------------------------------------------------
# A printer can disappear at three different moments. The FSM's
# PRINTING state branches on which printed_status comes back, so
# each path needs to be distinguishable:
#   * device unreachable from the start → is_available()=False
#   * USB error or hung printer mid-stream → PRINT_FAILED
#   * paper roll runs out partway through → PAPER_OUT_MID
# Mortality: would fail if any of these collapsed to PRINT_FAILED
# (which would lose the paper-out-mid signal in the audit trail —
# operators wouldn't know whether to refill paper or replace the
# unit), or if any of them raised instead of returning a
# PrintResult (which would crash the FSM's PRINTING handler).
@pytest.mark.asyncio
async def test_failure_modes_return_distinct_statuses() -> None:
    p = MockPrinterService()
    citizen = _make_citizen()
    session = _make_session(citizen)

    # 7a. Device unreachable: is_available() reports False.
    p.available = False
    assert p.is_available() is False
    p.available = True

    # 7b. Print-failed mid-stream: returns PRINT_FAILED, does NOT
    # land an entry in mock_print_history (the print never started).
    p.simulate_print_failed = True
    result = await p.print_session_report(
        session,
        citizen,
        [_meas(session, type="weight", value=61.0, unit="kg")],
        language="en",
    )
    assert result == PrintResult(False, PrintedStatus.PRINT_FAILED)
    assert p.mock_print_history == []
    # Auto-resets so a follow-up print attempt would succeed.
    assert p.simulate_print_failed is False

    # 7c. Paper-out mid-print: stops after the cut-off, records the
    # partial output (so tests can inspect what was printed before
    # the failure), and returns PAPER_OUT_MID.
    p.simulate_paper_out_after_lines = 3
    result = await p.print_session_report(
        session,
        citizen,
        [_meas(session, type="weight", value=61.0, unit="kg")],
        language="en",
    )
    assert result == PrintResult(False, PrintedStatus.PAPER_OUT_MID)
    assert len(p.mock_print_history) == 1
    partial = p.mock_print_history[0]
    # The partial only contains the first 3 lines — the closing
    # block, which is at the end of the receipt, must NOT appear in
    # a paper-out-mid result.
    assert "Thank you for your visit." not in partial
    assert p.simulate_paper_out_after_lines is None


# ---------------------------------------------------------------------
# 8. Factory selects mock vs real per Settings.MOCK_HARDWARE
# ---------------------------------------------------------------------
# The single sanctioned switch between mock and real printer
# implementations is Settings.MOCK_HARDWARE. Sniffing platform.machine()
# or env vars from elsewhere in the codebase is forbidden by
# CLAUDE.md; the factory in services/printer.py is the only
# consumer. This test pins that contract.
# Mortality: would fail if the factory grew a side path that picked
# the real driver under MOCK_HARDWARE=true (which would try to open
# /dev/usb on a dev laptop and crash), or vice versa.
def test_create_printer_service_respects_mock_hardware_switch() -> None:
    class _FakeSettings:
        MOCK_HARDWARE = True
        PRINTER_VENDOR_ID = 0x0416
        PRINTER_PRODUCT_ID = 0x5011

    mock_svc = create_printer_service(_FakeSettings())  # type: ignore[arg-type]
    assert isinstance(mock_svc, MockPrinterService)

    _FakeSettings.MOCK_HARDWARE = False
    real_svc = create_printer_service(_FakeSettings())  # type: ignore[arg-type]
    assert isinstance(real_svc, XprinterPrinterService)
